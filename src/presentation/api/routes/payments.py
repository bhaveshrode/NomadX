from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Depends, Path, Query, Header
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.payment.value_objects import PaymentId, PaymentIntentId, PaymentNetwork
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresPaymentRepository
from ....infrastructure.config.configuration import settings

from ....application.payment.commands import (
    DetectPaymentCommand,
    StartPaymentVerificationCommand,
    ConfirmPaymentCommand,
    RejectPaymentCommand
)
from ....domain.payment.payment import Payment
from ....domain.shared.result.result import Result

router = APIRouter(tags=["payments"])


# --- Schemas ---

class PaymentAssetSchema(BaseModel):
    code: str = Field(..., min_length=1)
    decimals: int = Field(..., ge=0)
    type: str = Field(default="DIGITAL_ASSET")


class DetectPaymentRequest(BaseModel):
    payment_id: Optional[str] = None
    payment_intent_id: Optional[str] = None
    observed_amount: Decimal = Field(...)
    asset: PaymentAssetSchema
    network_code: str = Field(..., min_length=1)
    network_type: str = Field(..., min_length=1)
    transaction_reference: str = Field(..., min_length=1)
    sender: str = Field(..., min_length=1)
    receiver: str = Field(..., min_length=1)
    observed_at: datetime
    organization_id: str = Field(..., min_length=1)


# --- Helpers ---

def _payment_to_dto(payment: Payment) -> dict:
    return {
        "id": payment.id.value,
        "payment_intent_id": payment.payment_intent_id.value if payment.payment_intent_id else None,
        "observed_amount": str(payment.observed_amount.amount),
        "asset": {
            "code": payment.asset.code,
            "type": payment.asset.type,
            "decimals": payment.asset.decimals
        },
        "network": payment.network.code,
        "transaction_reference": payment.transaction_reference,
        "sender": payment.sender,
        "receiver": payment.receiver,
        "status": payment.status.value,
        "reconciliation_status": payment.reconciliation_status.value,
        "observed_at": payment.observed_at.isoformat(),
        "created_at": payment.created_at.isoformat(),
        "updated_at": payment.updated_at.isoformat()
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


# --- Security Boundaries ---

async def verify_internal_auth(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret")
) -> None:
    """
    Guards internal endpoints from external unauthenticated HTTP access.
    """
    if not x_internal_secret or x_internal_secret != settings.internal_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal authorization secret."
        )


# --- Merchant Routes ---

@router.get("/api/payments", status_code=status.HTTP_200_OK)
async def list_payments(
    payment_intent_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    network: Optional[str] = Query(None),
    asset_code: Optional[str] = Query(None),
    transaction_reference: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Lists tenant-isolated payments, applying optional query filters.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentRepository(session)
        payments = await repo.find_by_organization(OrganizationId(ctx.organization_id))

    dtos = []
    for payment in payments:
        if payment_intent_id and (not payment.payment_intent_id or payment.payment_intent_id.value != payment_intent_id):
            continue
        if status_filter and payment.status.value != status_filter:
            continue
        if network and payment.network.code != network:
            continue
        if asset_code and payment.asset.code != asset_code:
            continue
        if transaction_reference and payment.transaction_reference != transaction_reference:
            continue
        if from_date and payment.created_at < from_date:
            continue
        if to_date and payment.created_at > to_date:
            continue
        dtos.append(_payment_to_dto(payment))

    return dtos[offset : offset + limit]


@router.get("/api/payments/{id}", status_code=status.HTTP_200_OK)
async def get_payment(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves a specific payment details, enforcing tenant boundaries.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentRepository(session)
        payment = await repo.find_by_id(PaymentId(id))

    if not payment or payment.organization_id.value != ctx.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")

    return _payment_to_dto(payment)


# --- Internal Ingestion/Observation Routes ---

@router.post("/internal/payments/detect", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_internal_auth)])
async def detect_payment(
    payload: DetectPaymentRequest
):
    """
    Observation ingestion endpoint that tracks blockchain transaction events.
    """
    payment_id = payload.payment_id or str(uuid4())
    command = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=payload.payment_intent_id,
        observed_amount=payload.observed_amount,
        asset_code=payload.asset.code,
        asset_type=payload.asset.type,
        asset_decimals=payload.asset.decimals,
        network_code=payload.network_code,
        network_type=payload.network_type,
        transaction_reference=payload.transaction_reference,
        sender=payload.sender,
        receiver=payload.receiver,
        observed_at=payload.observed_at,
        organization_id=payload.organization_id
    )

    ctx = ExecutionContext(user_id="internal-system", organization_id=payload.organization_id, role="SYSTEM")

    async with container.detect_payment_handler() as handler:
        result = await handler.handle(command, ctx)

    payment = _handle_result(result)
    return _payment_to_dto(payment)


@router.post("/internal/payments/{id}/verify", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_internal_auth)])
async def verify_payment(
    id: str = Path(...)
):
    """
    Start payment verification logic inside NomadX.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentRepository(session)
        payment = await repo.find_by_id(PaymentId(id))

    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found.")

    ctx = ExecutionContext(user_id="internal-system", organization_id=payment.organization_id.value, role="SYSTEM")
    command = StartPaymentVerificationCommand(payment_id=id)

    async with container.start_payment_verification_handler() as handler:
        result = await handler.handle(command, ctx)

    _handle_result(result)
    return {"status": "ok"}


@router.post("/internal/payments/{id}/confirm", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_internal_auth)])
async def confirm_payment(
    id: str = Path(...)
):
    """
    Confirm payment settles funds check according to rules.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentRepository(session)
        payment = await repo.find_by_id(PaymentId(id))

    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found.")

    ctx = ExecutionContext(user_id="internal-system", organization_id=payment.organization_id.value, role="SYSTEM")
    command = ConfirmPaymentCommand(payment_id=id)

    async with container.confirm_payment_handler() as handler:
        result = await handler.handle(command, ctx)

    _handle_result(result)
    return {"status": "ok"}


@router.post("/internal/payments/{id}/reject", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_internal_auth)])
async def reject_payment(
    id: str = Path(...)
):
    """
    Rejects payment if verification rules fail.
    """
    async with session_manager.session() as session:
        repo = PostgresPaymentRepository(session)
        payment = await repo.find_by_id(PaymentId(id))

    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found.")

    ctx = ExecutionContext(user_id="internal-system", organization_id=payment.organization_id.value, role="SYSTEM")
    command = RejectPaymentCommand(payment_id=id)

    async with container.reject_payment_handler() as handler:
        result = await handler.handle(command, ctx)

    _handle_result(result)
    return {"status": "ok"}


from ....infrastructure.database.repositories import PostgresPaymentAllocationRepository

@router.get("/api/payments/{id}/allocations", status_code=status.HTTP_200_OK)
async def get_payment_allocations(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    async with session_manager.session() as session:
        payment_repo = PostgresPaymentRepository(session)
        payment = await payment_repo.find_by_id(PaymentId(id))
        if not payment or payment.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=404, detail="Payment not found.")

        allocation_repo = PostgresPaymentAllocationRepository(session)
        allocations = await allocation_repo.find_by_payment_id(payment.id)

        return [
            {
                "allocation_id": alloc.id.value,
                "payment_id": alloc.payment_id.value,
                "invoice_id": alloc.invoice_id.value,
                "allocated_amount": str(alloc.allocated_amount.amount),
                "currency": alloc.allocated_amount.currency.code,
                "status": alloc.status,
                "allocated_at": alloc.created_at.isoformat()
            }
            for alloc in allocations
        ]
