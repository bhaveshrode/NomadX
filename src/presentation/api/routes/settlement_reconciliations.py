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
from ....domain.shared.result.result import ResultError
from ....application.settlement_reconciliation.commands import (
    CreateSettlementCommand,
    OpenSettlementCommand,
    AddSettlementEntryCommand,
    AddSettlementAdjustmentCommand,
    BeginReconciliationCommand,
    ReconcileSettlementCommand,
    RecordSettlementDiscrepancyCommand,
    ResolveSettlementDiscrepancyCommand,
    CompleteSettlementCommand,
    CancelSettlementCommand
)

router = APIRouter(prefix="/api/settlement-reconciliations", tags=["settlement-reconciliations"])


# --- Schemas ---

class CreateSettlementRequest(BaseModel):
    reference: str = Field(..., min_length=1)
    merchant_reference: str = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    currency_code: str = Field(..., min_length=3, max_length=6)
    idempotency_key: Optional[str] = None


class AddSettlementEntryRequest(BaseModel):
    entry_reference: str = Field(..., min_length=1)
    source_transaction_reference: str = Field(..., min_length=1)
    source_context_type: str = Field(..., min_length=1)
    gross_amount: Decimal
    fees_amount: Decimal
    adjustments_amount: Decimal
    net_amount: Decimal
    currency_code: str = Field(..., min_length=3, max_length=6)


class AddSettlementAdjustmentRequest(BaseModel):
    adjustment_id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    amount: Decimal
    currency_code: str = Field(..., min_length=3, max_length=6)


class DiscrepancyItem(BaseModel):
    entry_reference: str = Field(..., min_length=1)
    discrepancy_type: str = Field(..., min_length=1)
    expected_amount: Decimal
    actual_amount: Decimal
    description: str = Field(..., min_length=1)
    resolved: bool = False


class ReconcileSettlementRequest(BaseModel):
    status: str = Field(..., min_length=1)
    discrepancies: List[DiscrepancyItem]
    verified_at: datetime
    description: str = Field(..., min_length=1)


class RecordDiscrepancyRequest(BaseModel):
    entry_reference: str = Field(..., min_length=1)
    discrepancy_type: str = Field(..., min_length=1)
    expected_amount: Decimal
    actual_amount: Decimal
    description: str = Field(..., min_length=1)


# --- Helpers & Error Mapping ---

def map_error_to_http(error: ResultError):
    if error.code in ("SETTLEMENT_NOT_FOUND", "UNAUTHORIZED_ORGANIZATION_ACCESS", "SETTLEMENT_DISCREPANCY_NOT_FOUND"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    elif error.code in (
        "DUPLICATE_SETTLEMENT",
        "INVALID_SETTLEMENT_STATE",
        "SETTLEMENT_ALREADY_COMPLETED",
        "SETTLEMENT_ALREADY_CANCELLED",
        "DUPLICATE_SETTLEMENT_ENTRY",
        "SETTLEMENT_NOT_BALANCED",
        "SETTLEMENT_DISCREPANCY_EXISTS",
        "INVALID_RECONCILIATION_STATE"
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message)
    elif error.code in (
        "INVALID_SETTLEMENT_DATA",
        "SETTLEMENT_ENTRY_INVALID",
        "SETTLEMENT_CURRENCY_MISMATCH"
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message)
    
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error.message)


def require_roles(allowed_roles: List[str]):
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency

require_write = require_roles(["ADMIN", "OWNER"])
require_read = require_roles(["MEMBER", "ADMIN", "OWNER"])


# --- Endpoints ---

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_settlement(
    payload: CreateSettlementRequest,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_write)
):
    idem = idempotency_key_header or payload.idempotency_key or str(uuid4())

    # Precheck idempotency key to return 200 OK if already exists
    async with container.settlement_reconciliation_query_service() as query_service:
        all_settlements = await query_service.list_settlements(OrganizationId(ctx.organization_id))
        for s in all_settlements:
            if s["idempotency_key"] == idem:
                return JSONResponse(status_code=status.HTTP_200_OK, content=s)

    settlement_id = str(uuid4())
    command = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=ctx.organization_id,
        reference=payload.reference,
        merchant_reference=payload.merchant_reference,
        start_date=payload.start_date,
        end_date=payload.end_date,
        currency_code=payload.currency_code,
        idempotency_key=idem
    )

    async with container.create_settlement_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)

    if result.is_failure:
        map_error_to_http(result.error)

    # Return read-side DTO representation
    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), result.value.id.value
        )
    return dto


@router.get("", status_code=status.HTTP_200_OK)
async def list_settlements(
    reference: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    currency: Optional[str] = Query(None),
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
    merchant_reference: Optional[str] = Query(None),
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        results = await query_service.list_settlements(
            organization_id=OrganizationId(ctx.organization_id),
            reference=reference,
            status=status_filter,
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            merchant_reference=merchant_reference,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order
        )
    return results


@router.get("/failed", status_code=status.HTTP_200_OK)
async def get_failed_settlements(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_read)
):
    """Retrieves failed or dead-lettered settlement execution batches."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.list_failed_settlements(
            OrganizationId(ctx.organization_id), limit, offset
        )
    return res


@router.get("/dead-letter", status_code=status.HTTP_200_OK)
async def get_dead_letter_settlements(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_read)
):
    """Retrieves dead-lettered settlement execution batches."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.list_dead_letter_settlements(
            OrganizationId(ctx.organization_id), limit, offset
        )
    return res


@router.get("/metrics", status_code=status.HTTP_200_OK)
async def get_settlement_metrics(
    ctx: ExecutionContext = Depends(require_read)
):
    """Retrieves operational metrics, rates, and latency for dashboard view."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.get_settlement_metrics(
            OrganizationId(ctx.organization_id)
        )
    return res


@router.get("/audit", status_code=status.HTTP_200_OK)
async def get_settlement_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Retrieves operational audit logs for administrative interventions."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.get_settlement_audit_logs(
            OrganizationId(ctx.organization_id), limit, offset
        )
    return res


@router.get("/health", status_code=status.HTTP_200_OK)
async def get_settlement_health(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Calculates operational health diagnoses and alert status."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.get_settlement_health(
            OrganizationId(ctx.organization_id)
        )
    return res


@router.get("/{id}", status_code=status.HTTP_200_OK)
async def get_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    if not dto:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")
    return dto


@router.post("/{id}/open", status_code=status.HTTP_200_OK)
async def open_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = OpenSettlementCommand(settlement_id=id, organization_id=ctx.organization_id)
    async with container.open_settlement_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.post("/{id}/complete", status_code=status.HTTP_200_OK)
async def complete_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = CompleteSettlementCommand(settlement_id=id, organization_id=ctx.organization_id)
    async with container.complete_settlement_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.post("/{id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_settlement(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = CancelSettlementCommand(settlement_id=id, organization_id=ctx.organization_id)
    async with container.cancel_settlement_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


# We declare direct FastAPI request injection to support JSON bodies
@router.post("/{id}/entries", status_code=status.HTTP_200_OK)
async def add_settlement_entry(
    payload: AddSettlementEntryRequest,
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = AddSettlementEntryCommand(
        settlement_id=id,
        organization_id=ctx.organization_id,
        entry_reference=payload.entry_reference,
        source_transaction_reference=payload.source_transaction_reference,
        source_context_type=payload.source_context_type,
        gross_amount=payload.gross_amount,
        fees_amount=payload.fees_amount,
        adjustments_amount=payload.adjustments_amount,
        net_amount=payload.net_amount,
        currency_code=payload.currency_code
    )
    async with container.add_settlement_entry_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.get("/{id}/entries", status_code=status.HTTP_200_OK)
async def get_settlement_entries(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        # Verify settlement exists and belongs to this organization
        settlement = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
        if not settlement:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        results = await query_service.get_settlement_entries(
            OrganizationId(ctx.organization_id), id
        )
    return results


@router.post("/{id}/adjustments", status_code=status.HTTP_200_OK)
async def add_settlement_adjustment(
    payload: AddSettlementAdjustmentRequest,
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = AddSettlementAdjustmentCommand(
        settlement_id=id,
        organization_id=ctx.organization_id,
        adjustment_id=payload.adjustment_id,
        type=payload.type,
        description=payload.description,
        amount=payload.amount,
        currency_code=payload.currency_code
    )
    async with container.add_settlement_adjustment_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.get("/{id}/adjustments", status_code=status.HTTP_200_OK)
async def get_settlement_adjustments(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        settlement = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
        if not settlement:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        results = await query_service.get_settlement_adjustments(
            OrganizationId(ctx.organization_id), id
        )
    return results


@router.post("/{id}/reconciliation/start", status_code=status.HTTP_200_OK)
async def start_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = BeginReconciliationCommand(settlement_id=id, organization_id=ctx.organization_id)
    async with container.begin_reconciliation_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.post("/{id}/reconciliation/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_settlement(
    payload: ReconcileSettlementRequest,
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    discrepancies = [
        {
            "entry_reference": d.entry_reference,
            "discrepancy_type": d.discrepancy_type,
            "expected_amount": d.expected_amount,
            "actual_amount": d.actual_amount,
            "description": d.description,
            "resolved": d.resolved
        }
        for d in payload.discrepancies
    ]

    command = ReconcileSettlementCommand(
        settlement_id=id,
        organization_id=ctx.organization_id,
        status=payload.status,
        discrepancies=discrepancies,
        verified_at=payload.verified_at,
        description=payload.description
    )
    async with container.reconcile_settlement_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.get("/{id}/reconciliation", status_code=status.HTTP_200_OK)
async def get_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.get_reconciliation_result(
            OrganizationId(ctx.organization_id), id
        )
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reconciliation result not found.")
    return res


@router.post("/{id}/discrepancies", status_code=status.HTTP_200_OK)
async def record_discrepancy(
    payload: RecordDiscrepancyRequest,
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    command = RecordSettlementDiscrepancyCommand(
        settlement_id=id,
        organization_id=ctx.organization_id,
        entry_reference=payload.entry_reference,
        discrepancy_type=payload.discrepancy_type,
        expected_amount=payload.expected_amount,
        actual_amount=payload.actual_amount,
        description=payload.description
    )
    async with container.record_settlement_discrepancy_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.get("/{id}/discrepancies", status_code=status.HTTP_200_OK)
async def get_settlement_discrepancies(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    async with container.settlement_reconciliation_query_service() as query_service:
        settlement = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
        if not settlement:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        results = await query_service.get_settlement_discrepancies(
            OrganizationId(ctx.organization_id), id
        )
    return results


@router.post("/{id}/discrepancies/{discrepancy_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_discrepancy(
    id: str = Path(...),
    discrepancy_id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    # discrepancy_id in path is mapped to entry_reference of the discrepancy to be resolved
    command = ResolveSettlementDiscrepancyCommand(
        settlement_id=id,
        organization_id=ctx.organization_id,
        entry_reference=discrepancy_id
    )
    async with container.resolve_settlement_discrepancy_handler() as handler:
        result = await handler.handle(command, ctx)
    if result.is_failure:
        map_error_to_http(result.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(
            OrganizationId(ctx.organization_id), id
        )
    return dto


@router.post("/process", status_code=status.HTTP_200_OK)
async def process_settlements(
    ctx: ExecutionContext = Depends(require_write)
):
    async with container.settlement_automation_worker() as worker:
        count = await worker.process_eligible_settlements()
    return {"status": "success", "processed_batches": count}


@router.post("/{id}/reconcile", status_code=status.HTTP_200_OK)
async def force_reconcile(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    async with container.settlement_reconciliation_automation_service() as service:
        res = await service.reconcile_settlement(id, ctx.organization_id, ctx)
    if res.is_failure:
        map_error_to_http(res.error)

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(OrganizationId(ctx.organization_id), id)
    return dto


@router.post("/{id}/retry", status_code=status.HTTP_200_OK)
async def retry_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    from ....infrastructure.database.session import session_manager
    from ....infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
    from ....domain.settlement_reconciliation.value_objects import SettlementId

    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlement = await repo.get_by_id(SettlementId(id), lock=True)
        if not settlement or settlement.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")
        
        res = settlement.reset_retry()
        if res.is_failure:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.error.message)

        await repo.save(settlement)
        await session.commit()

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(OrganizationId(ctx.organization_id), id)
    return dto


@router.post("/{id}/pause", status_code=status.HTTP_200_OK)
async def pause_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    from ....infrastructure.database.session import session_manager
    from ....infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
    from ....domain.settlement_reconciliation.value_objects import SettlementId

    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlement = await repo.get_by_id(SettlementId(id), lock=True)
        if not settlement or settlement.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        res = settlement.pause()
        if res.is_failure:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.error.message)

        await repo.save(settlement)
        await session.commit()

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(OrganizationId(ctx.organization_id), id)
    return dto


@router.post("/{id}/resume", status_code=status.HTTP_200_OK)
async def resume_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    from ....infrastructure.database.session import session_manager
    from ....infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
    from ....domain.settlement_reconciliation.value_objects import SettlementId

    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlement = await repo.get_by_id(SettlementId(id), lock=True)
        if not settlement or settlement.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        res = settlement.resume()
        if res.is_failure:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.error.message)

        await repo.save(settlement)
        await session.commit()

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(OrganizationId(ctx.organization_id), id)
    return dto


@router.post("/{id}/replay", status_code=status.HTTP_200_OK)
async def replay_reconciliation(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_write)
):
    from ....infrastructure.database.session import session_manager
    from ....infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
    from ....domain.settlement_reconciliation.settlement import SettlementStatus
    from ....domain.settlement_reconciliation.reconciliation import ReconciliationStatus
    from ....domain.settlement_reconciliation.value_objects import SettlementId

    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        settlement = await repo.get_by_id(SettlementId(id), lock=True)
        if not settlement or settlement.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found.")

        if settlement.status == SettlementStatus.COMPLETED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot replay a completed settlement.")

        settlement.replay_processing()
        settlement._props["status"] = SettlementStatus.OPEN
        settlement._props["reconciliation_state"] = ReconciliationStatus.PENDING
        settlement._props["reconciliation_result"] = None
        
        await repo.save(settlement)
        await session.commit()

    async with container.settlement_reconciliation_query_service() as query_service:
        dto = await query_service.get_settlement(OrganizationId(ctx.organization_id), id)
    return dto


@router.get("/{id}/timeline", status_code=status.HTTP_200_OK)
async def get_settlement_timeline(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_read)
):
    """Retrieves chronological timeline for a specific settlement batch."""
    async with container.settlement_reconciliation_query_service() as query_service:
        res = await query_service.get_settlement_timeline(
            OrganizationId(ctx.organization_id), id
        )
    return res
