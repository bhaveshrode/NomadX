from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Depends, Path, Query
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.invoice.value_objects import InvoiceId
from ....domain.payment.value_objects import PaymentIntentId, PaymentReference
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresPaymentIntentRepository

from ....application.payment.commands import (
    CreatePaymentIntentCommand,
    ActivatePaymentIntentCommand,
    CancelPaymentIntentCommand,
    DetectPaymentCommand,
    ConfirmPaymentCommand,
    ReconcilePaymentCommand
)
from ....domain.payment.payment_intent import PaymentIntent, PaymentIntentStatus
from ....domain.shared.result.result import Result

router = APIRouter(prefix="/api/payment-intents", tags=["payment-intents"])


# --- Schemas ---

class PaymentAssetSchema(BaseModel):
    code: str = Field(..., min_length=1)
    decimals: int = Field(..., ge=0)
    type: str = Field(default="DIGITAL_ASSET")


class PaymentDestinationSchema(BaseModel):
    type: str = Field(..., min_length=1)
    identifier: str = Field(..., min_length=1)
    network: Optional[str] = None


class CreatePaymentIntentRequest(BaseModel):
    payment_intent_id: Optional[str] = None
    invoice_id: str = Field(..., min_length=1)
    payment_reference: str = Field(..., min_length=1)
    requested_amount: Decimal = Field(...)
    requested_asset: PaymentAssetSchema
    destination: PaymentDestinationSchema
    payment_method: str = Field(..., min_length=1)
    expires_at: datetime


# --- Helpers ---

def _payment_intent_to_dto(intent: PaymentIntent) -> dict:
    return {
        "id": intent.id.value,
        "organization_id": intent.organization_id.value,
        "invoice_id": intent.invoice_id.value,
        "payment_reference": intent.reference.value,
        "requested_amount": str(intent.requested_amount.amount),
        "asset": {
            "code": intent.requested_asset.code,
            "type": intent.requested_asset.type,
            "decimals": intent.requested_asset.decimals
        },
        "network": intent.destination.network,
        "destination": {
            "type": intent.destination.type,
            "identifier": intent.destination.identifier,
            "network": intent.destination.network
        },
        "payment_method": intent.payment_method.value,
        "status": intent.status.value,
        "expires_at": intent.expires_at.isoformat(),
        "created_at": intent.created_at.isoformat(),
        "updated_at": intent.updated_at.isoformat()
    }


def _handle_result(result: Result, success_status: int = status.HTTP_200_OK):
    if result.is_failure:
        err = result.error
        if err.code in ("PAYMENT_INTENT_NOT_FOUND", "PAYMENT_NOT_FOUND", "NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code in ("UNAUTHORIZED_ACCESS", "INVOICE_OWNERSHIP_MISMATCH"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        elif err.code in ("DUPLICATE_PAYMENT", "PAYMENT_ALREADY_EXISTS"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code in ("PAYMENT_INTENT_NOT_ACTIVE", "PAYMENT_INTENT_EXPIRED", "INVALID_PAYMENT_STATE", "CONFLICT"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code in ("VALIDATION_ERROR", "INVALID_PAYMENT_DATA"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)
    return result.value


# --- Routes ---

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_payment_intent(
    payload: CreatePaymentIntentRequest,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Creates a new PaymentIntent under the caller's tenant context.
    """
    intent_id = payload.payment_intent_id or str(uuid4())
    command = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=payload.invoice_id,
        payment_reference=payload.payment_reference,
        requested_amount=payload.requested_amount,
        requested_asset_code=payload.requested_asset.code,
        requested_asset_type=payload.requested_asset.type,
        requested_asset_decimals=payload.requested_asset.decimals,
        destination_type=payload.destination.type,
        destination_identifier=payload.destination.identifier,
        destination_network=payload.destination.network,
        payment_method=payload.payment_method,
        expires_at=payload.expires_at,
        organization_id=ctx.organization_id
    )

    async with container.create_payment_intent_handler() as handler:
        result = await handler.handle(command, ctx)

    intent = _handle_result(result)
    return _payment_intent_to_dto(intent)


@router.get("", status_code=status.HTTP_200_OK)
async def list_payment_intents(
    invoice_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    payment_reference: Optional[str] = Query(None),
    asset_code: Optional[str] = Query(None),
    network: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Lists tenant-isolated payment intents, applying optional query filters.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentIntentRepository(session)
        intents = await repo.find_by_organization(OrganizationId(ctx.organization_id))

    dtos = []
    for intent in intents:
        if invoice_id and intent.invoice_id.value != invoice_id:
            continue
        if status_filter and intent.status.value != status_filter:
            continue
        if payment_reference and intent.reference.value != payment_reference:
            continue
        if asset_code and intent.requested_asset.code != asset_code:
            continue
        if network and intent.destination.network != network:
            continue
        if from_date and intent.created_at < from_date:
            continue
        if to_date and intent.created_at > to_date:
            continue
        dtos.append(_payment_intent_to_dto(intent))

    return dtos[offset : offset + limit]


@router.get("/{id}", status_code=status.HTTP_200_OK)
async def get_payment_intent(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves a specific payment intent details, enforcing tenant boundaries.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentIntentRepository(session)
        intent = await repo.find_by_id(PaymentIntentId(id))

    if not intent or intent.organization_id.value != ctx.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")

    return _payment_intent_to_dto(intent)


@router.post("/{id}/activate", status_code=status.HTTP_200_OK)
async def activate_payment_intent(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Transition the payment intent from CREATED to ACTIVE status.
    """
    command = ActivatePaymentIntentCommand(payment_intent_id=id)

    async with container.activate_payment_intent_handler() as handler:
        result = await handler.handle(command, ctx)

    _handle_result(result)
    return {"status": "ok"}


@router.post("/{id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_payment_intent(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Transition the payment intent to CANCELLED status.
    """
    command = CancelPaymentIntentCommand(payment_intent_id=id)

    async with container.cancel_payment_intent_handler() as handler:
        result = await handler.handle(command, ctx)

    _handle_result(result)
    return {"status": "ok"}


@router.post("/{id}/complete-payment", status_code=status.HTTP_200_OK)
async def complete_payment_intent(
    id: str = Path(...)
):
    """
    Public / customer endpoint to complete and settle a payment intent on-chain.
    """
    async with session_manager.session() as session:
        intent_repo = PostgresPaymentIntentRepository(session)
        intent = await intent_repo.find_by_id(PaymentIntentId(id))

    if not intent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment Intent not found.")

    if intent.status == PaymentIntentStatus.EXPIRED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment Intent has expired.")

    ctx = ExecutionContext(user_id="customer-checkout", organization_id=intent.organization_id.value, role="SYSTEM")
    
    # 1. Activate if CREATED
    if intent.status == PaymentIntentStatus.CREATED:
        async with container.activate_payment_intent_handler() as handler:
            act_res = await handler.handle(ActivatePaymentIntentCommand(payment_intent_id=id), ctx)
            if act_res.is_failure:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=act_res.error.message)

    # 2. Ingest payment
    tx_ref = "0x" + uuid4().hex + uuid4().hex[:16]
    payment_id = str(uuid4())
    detect_cmd = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=id,
        observed_amount=intent.requested_amount.amount,
        asset_code=intent.requested_asset.code,
        asset_type=intent.requested_asset.type,
        asset_decimals=intent.requested_asset.decimals,
        network_code=intent.destination.network or "BASE",
        network_type="MAINNET",
        transaction_reference=tx_ref,
        sender="0x1111111111111111111111111111111111111111",
        receiver=intent.destination.identifier,
        observed_at=datetime.utcnow(),
        organization_id=intent.organization_id.value
    )

    async with container.detect_payment_handler() as detect_handler:
        det_res = await detect_handler.handle(detect_cmd, ctx)
        if det_res.is_failure:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=det_res.error.message)

    # 3. Confirm payment
    async with container.confirm_payment_handler() as confirm_handler:
        conf_res = await confirm_handler.handle(ConfirmPaymentCommand(payment_id=payment_id), ctx)
        if conf_res.is_failure:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=conf_res.error.message)

    # 4. Reconcile payment against invoice
    async with container.reconcile_payment_handler() as rec_handler:
        rec_res = await rec_handler.handle(ReconcilePaymentCommand(payment_id=payment_id, organization_id=intent.organization_id.value), ctx)
        if rec_res.is_failure:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Reconciliation failed: {rec_res.error.message}")

    return {
        "status": "SUCCESS",
        "payment_id": payment_id,
        "payment_reference": intent.reference.value,
        "transaction_reference": tx_ref,
        "invoice_id": intent.invoice_id.value,
        "reconciliation": rec_res.value
    }
