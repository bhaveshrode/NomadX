from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Depends, Path, Query, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.payment.value_objects import PaymentId
from ....domain.settlement.value_objects import SettlementId, SettlementIdempotencyKey
from ....domain.settlement.settlement import Settlement, SettlementStatus
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresSettlementRepository, PostgresPaymentRepository
from ....application.settlement.services import SettlementAvailabilityService

from ....application.settlement.commands import (
    CreateSettlementCommand,
    StartSettlementProcessingCommand,
    SubmitSettlementCommand,
    ConfirmSettlementCommand,
    CompleteSettlementCommand,
    FailSettlementCommand,
    RetrySettlementCommand,
    CancelSettlementCommand
)
from ....domain.shared.result.result import Result

router = APIRouter(tags=["settlements"])


# --- Schemas ---

class CreateSettlementRequest(BaseModel):
    payment_id: str = Field(..., min_length=1)
    amount: str = Field(..., min_length=1, description="Exact amount as string to prevent float precision loss")
    asset: str = Field(..., min_length=1)
    network: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    method: str = Field(..., min_length=1)
    reference: str = Field(..., min_length=1)
    idempotency_key: Optional[str] = None


# --- Helpers & DTO Mappers ---

def _settlement_to_dto(s: Settlement) -> dict:
    failure_dto = None
    if s.failure:
        failure_dto = {
            "code": s.failure.code.value,
            "message": s.failure.message,
            "retryable": s.failure.retryable
        }

    return {
        "id": s.id.value,
        "organization_id": s.organization_id.value,
        "reference": s.reference.value,
        "payment_id": s.source.payment_id if s.source else None,
        "amount": str(s.amount.amount),
        "asset": s.asset.code,
        "network": s.network.value,
        "destination": s.destination.identifier,
        "method": s.method.value,
        "status": s.status.value,
        "transaction_reference": s.transaction_reference,
        "failure": failure_dto,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat()
    }


def _handle_result(result: Result, success_status: int = status.HTTP_200_OK):
    if result.is_failure:
        err = result.error
        if err.code in ("SETTLEMENT_NOT_FOUND", "PAYMENT_NOT_FOUND", "NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code in ("UNAUTHORIZED_ACCESS", "PAYMENT_OWNERSHIP_MISMATCH"):
            # Prevent resource-existence leakage, return 404
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        elif err.code in ("PAYMENT_NOT_ELIGIBLE", "INSUFFICIENT_SETTLEMENT_BALANCE", "DUPLICATE_SETTLEMENT_REFERENCE", "INVALID_SETTLEMENT_STATE", "SETTLEMENT_NOT_CANCELLABLE", "SETTLEMENT_NOT_RETRYABLE"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code == "DUPLICATE_SETTLEMENT_IDEMPOTENCY":
            # Will be handled separately to return 200 OK, but default to 409 if uncaught
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code in ("VALIDATION_ERROR", "INVALID_SETTLEMENT_DESTINATION"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)
    return result.value


# --- Authorization Dependency ---

def require_roles(allowed_roles: List[str]):
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency


# --- Endpoints ---

@router.post("/api/settlements", status_code=status.HTTP_201_CREATED)
async def create_settlement(
    payload: CreateSettlementRequest,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Creates a new settlement instruction from a confirmed Payment.
    Supports HTTP Idempotency via header or payload idempotency key.
    """
    idem = idempotency_key_header or payload.idempotency_key or str(uuid4())

    # Pre-check idempotency key to return 200 OK if already exists
    async with session_manager.session() as session:
        settlement_repo = PostgresSettlementRepository(session)
        existing = await settlement_repo.find_by_idempotency_key(
            OrganizationId(ctx.organization_id),
            SettlementIdempotencyKey(idem)
        )
        if existing:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_settlement_to_dto(existing)
            )

    # Convert string-based amount to Decimal
    try:
        amount_dec = Decimal(payload.amount)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid amount format. Must be a valid decimal string."
        )

    settlement_id = str(uuid4())
    command = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=ctx.organization_id,
        payment_id=payload.payment_id,
        amount=amount_dec,
        asset_code=payload.asset,
        asset_type="DIGITAL_ASSET", # Defaulting to digital asset
        asset_decimals=6, # Default decimals, handler will validate/adjust
        network=payload.network,
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier=payload.destination,
        method=payload.method,
        reference=payload.reference,
        idempotency_key=idem
    )

    async with container.create_settlement_handler() as handler:
        result = await handler.handle(command, ctx)

    if result.is_failure and result.error.code == "DUPLICATE_SETTLEMENT_IDEMPOTENCY":
        # Concurrent race occurred: load and return the existing settlement
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            reloaded = await settlement_repo.find_by_idempotency_key(
                OrganizationId(ctx.organization_id),
                SettlementIdempotencyKey(idem)
            )
            if reloaded:
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content=_settlement_to_dto(reloaded)
                )

    settlement = _handle_result(result)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_settlement_to_dto(settlement)
    )


@router.get("/api/settlements", status_code=status.HTTP_200_OK)
async def list_settlements(
    status_filter: Optional[str] = Query(None, alias="status"),
    payment_id: Optional[str] = Query(None),
    reference: Optional[str] = Query(None),
    network: Optional[str] = Query(None),
    asset: Optional[str] = Query(None),
    destination: Optional[str] = Query(None),
    created_from: Optional[datetime] = Query(None),
    created_to: Optional[datetime] = Query(None),
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Lists tenant-isolated settlements, applying query filters.
    """
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlements = await repo.find_by_organization(OrganizationId(ctx.organization_id))

    dtos = []
    for s in settlements:
        if status_filter and s.status.value != status_filter:
            continue
        if payment_id and (not s.source or s.source.identifier != payment_id):
            continue
        if reference and s.reference.value != reference:
            continue
        if network and s.network.value != network:
            continue
        if asset and s.asset.code != asset:
            continue
        if destination and s.destination.identifier != destination:
            continue
        if created_from and s.created_at < created_from:
            continue
        if created_to and s.created_at > created_to:
            continue
        dtos.append(_settlement_to_dto(s))

    return dtos[offset : offset + limit]


@router.get("/api/settlements/{id}", status_code=status.HTTP_200_OK)
async def get_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Retrieves a specific settlement details. Enforces tenant boundary
    by returning 404 for foreign settlements.
    """
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlement = await repo.get_by_id(SettlementId(id))

    if not settlement or settlement.organization_id.value != ctx.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found."
        )

    return _settlement_to_dto(settlement)


@router.get("/api/payments/{payment_id}/settlements", status_code=status.HTTP_200_OK)
async def get_payment_settlements(
    payment_id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Returns all settlements associated with a specific Payment, including
    aggregations for payment, settled, and available amounts.
    """
    async with session_manager.session() as session:
        payment_repo = PostgresPaymentRepository(session)
        payment = await payment_repo.find_by_id(PaymentId(payment_id))
        if not payment or payment.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=404, detail="Resource not found.")

        settlement_repo = PostgresSettlementRepository(session)
        settlements = await settlement_repo.find_by_payment_id(payment_id)

    # Filter to current org and calculate settled amount
    org_settlements = []
    settled_amount = Decimal("0.00")
    for s in settlements:
        if s.organization_id.value == ctx.organization_id:
            org_settlements.append(_settlement_to_dto(s))
            if s.status not in (SettlementStatus.CANCELLED, SettlementStatus.FAILED):
                settled_amount += s.amount.amount

    payment_amount = payment.observed_amount.amount
    available_amount = payment_amount - settled_amount
    if available_amount < 0:
        available_amount = Decimal("0.00")

    return {
        "payment_amount": str(payment_amount),
        "settled_amount": str(settled_amount),
        "available_amount": str(available_amount),
        "settlements": org_settlements
    }


@router.get("/api/payments/{payment_id}/settlement-availability", status_code=status.HTTP_200_OK)
async def get_settlement_availability(
    payment_id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Exposes availability checks for a given payment.
    """
    async with session_manager.session() as session:
        payment_repo = PostgresPaymentRepository(session)
        settlement_repo = PostgresSettlementRepository(session)
        
        # Verify payment exists and belongs to organization
        payment = await payment_repo.find_by_id(PaymentId(payment_id))
        if not payment or payment.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=404, detail="Resource not found.")

        avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
        try:
            avail = await avail_svc.get_availability(OrganizationId(ctx.organization_id), payment_id)
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err))

    return {
        "payment_id": payment_id,
        "payment_amount": str(avail.payment_amount),
        "settled_amount": str(avail.already_settled),
        "available_amount": str(avail.available_amount),
        "currency": avail.currency
    }


@router.post("/api/settlements/{id}/process", status_code=status.HTTP_200_OK)
async def process_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Request that a pending or retrying settlement begin processing.
    """
    command = StartSettlementProcessingCommand(
        settlement_id=id,
        organization_id=ctx.organization_id
    )

    async with container.start_settlement_processing_handler() as handler:
        result = await handler.handle(command, ctx)

    settlement = _handle_result(result)
    return _settlement_to_dto(settlement)


@router.post("/api/settlements/{id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Cancels a pending settlement instruction.
    """
    command = CancelSettlementCommand(
        settlement_id=id,
        organization_id=ctx.organization_id
    )

    async with container.cancel_settlement_handler() as handler:
        result = await handler.handle(command, ctx)

    settlement = _handle_result(result)
    return _settlement_to_dto(settlement)


@router.post("/api/settlements/{id}/retry", status_code=status.HTTP_200_OK)
async def retry_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Retries a failed retryable settlement.
    """
    command = RetrySettlementCommand(
        settlement_id=id,
        organization_id=ctx.organization_id
    )

    async with container.retry_settlement_handler() as handler:
        result = await handler.handle(command, ctx)

    settlement = _handle_result(result)
    return _settlement_to_dto(settlement)
