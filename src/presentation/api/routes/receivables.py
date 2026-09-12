from typing import List, Optional
from decimal import Decimal
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Path, Query, Header, status, Response
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.receivable.value_objects import ReceivableId
from ....domain.receivable.receivable import Receivable
from ....domain.invoice import InvoiceId
from ....domain.shared.clock.clock import Clock
from ....domain.shared.result.result import ResultError
from ....application.receivable.commands import (
    CreateReceivableCommand,
    AllocatePaymentCommand,
    MarkReceivableOverdueCommand,
    SettleReceivableCommand,
    WriteOffReceivableCommand,
    AdvanceDunningStageCommand,
    UpdateReceivableAgingCommand
)

router = APIRouter(tags=["receivables"])


# --- API DTOs ---

class CreateReceivableRequest(BaseModel):
    receivable_id: str
    invoice_id: str
    customer_reference: Optional[str] = None
    invoice_reference: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    due_date: Optional[datetime] = None
    metadata: Optional[dict] = None


class AllocatePaymentRequest(BaseModel):
    payment_id: str
    allocated_amount: Decimal
    currency_code: str
    allocation_reference: str


class WriteOffReceivableRequest(BaseModel):
    reason: str


class AdvanceDunningRequest(BaseModel):
    next_stage: str
    next_attempt_at: Optional[datetime] = None


# --- Error Mapping ---

def map_error_to_http(error: ResultError):
    if error.code in ("RECEIVABLE_NOT_FOUND", "UNAUTHORIZED_ORGANIZATION_ACCESS", "PAYMENT_NOT_FOUND"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    elif error.code in (
        "DUPLICATE_RECEIVABLE",
        "INVALID_RECEIVABLE_STATE",
        "RECEIVABLE_ALREADY_SETTLED",
        "RECEIVABLE_ALREADY_WRITTEN_OFF",
        "ALLOCATION_EXCEEDS_OUTSTANDING",
        "INVALID_DUNNING_TRANSITION"
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message)
    elif error.code in ("INVALID_RECEIVABLE_DATA", "PAYMENT_ALLOCATION_INVALID"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message)
    
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error.message)


# --- Role Guard ---

def require_roles(allowed_roles: List[str]):
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency


def _receivable_to_dto(receivable: Receivable) -> dict:
    return {
        "id": receivable.id.value,
        "organization_id": receivable.organization_id.value,
        "customer_id": receivable.customer_id.value,
        "customer_reference": receivable.customer_reference.value if hasattr(receivable.customer_reference, "value") else str(receivable.customer_reference),
        "invoice_id": receivable.invoice_id.value,
        "invoice_reference": receivable.invoice_reference.value if hasattr(receivable.invoice_reference, "value") else str(receivable.invoice_reference),
        "reference": receivable.reference.value if hasattr(receivable.reference, "value") else str(receivable.reference),
        "original_amount": f"{receivable.original_amount.amount:.2f}",
        "paid_amount": f"{receivable.paid_amount.amount:.2f}",
        "allocated_amount": f"{receivable.allocated_amount.amount:.2f}",
        "outstanding_amount": f"{receivable.outstanding_amount.amount:.2f}",
        "currency": receivable.currency.code,
        "due_date": receivable.due_date.isoformat(),
        "status": receivable.status.value,
        "payment_status": receivable.payment_status.value,
        "collection_status": receivable.collection_status.value,
        "aging_bucket": receivable.calculate_aging(Clock).value,
        "dunning_stage": receivable.dunning_state.stage.value if hasattr(receivable.dunning_state, "stage") else str(receivable.dunning_state),
        "created_at": receivable.created_at.isoformat(),
        "updated_at": receivable.updated_at.isoformat(),
        "allocations": [
            {
                "id": a.allocation_reference,
                "payment_reference": a.payment_id,
                "allocated_amount": f"{a.allocated_amount.amount:.2f}",
                "currency": a.allocated_amount.currency.code,
                "allocated_at": a.allocated_at.isoformat()
            } for a in receivable.allocations
        ]
    }


# --- Endpoints ---

@router.post("/api/receivables", status_code=status.HTTP_201_CREATED, response_model=dict)
async def create_receivable(
    payload: CreateReceivableRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Creates a new receivable, supports tenant-scoped idempotency."""
    # Pre-check idempotency: Check if receivable already exists for this invoice
    async with container.receivable_repository() as repo:
        existing = await repo.find_by_invoice_id(InvoiceId(payload.invoice_id))
        if existing:
            # Enforce org boundary validation (prevent leaking info from other orgs)
            if existing.organization_id.value != ctx.organization_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
            response.status_code = status.HTTP_200_OK
            return _receivable_to_dto(existing)

    cmd = CreateReceivableCommand(
        receivable_id=payload.receivable_id,
        organization_id=ctx.organization_id,
        invoice_id=payload.invoice_id
    )

    async with container.create_receivable_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)

        response.status_code = status.HTTP_201_CREATED
        return _receivable_to_dto(res.value)


@router.get("/api/receivables/aging", response_model=dict)
async def get_aging_report(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves organization-wide summary of outstanding receivables grouped by aging buckets."""
    async with container.receivable_query_service() as q_service:
        return await q_service.get_aging_summary(OrganizationId(ctx.organization_id))


@router.get("/api/receivables/aging-summary", response_model=dict)
async def get_org_aging_summary(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves organization-wide aging breakdown summary."""
    async with container.receivable_query_service() as q_service:
        return await q_service.get_aging_summary(OrganizationId(ctx.organization_id))


@router.get("/api/receivables", response_model=List[dict])
async def get_receivables(
    status: Optional[str] = Query(None),
    customer_reference: Optional[str] = Query(None),
    invoice_reference: Optional[str] = Query(None),
    aging_bucket: Optional[str] = Query(None),
    dunning_stage: Optional[str] = Query(None),
    overdue: Optional[bool] = Query(None),
    created_from: Optional[datetime] = Query(None),
    created_to: Optional[datetime] = Query(None),
    due_from: Optional[datetime] = Query(None),
    due_to: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Lists organization receivables with optional query filters."""
    async with container.receivable_query_service() as q_service:
        return await q_service.list_receivables(
            organization_id=OrganizationId(ctx.organization_id),
            status=status,
            customer_reference=customer_reference,
            invoice_reference=invoice_reference,
            aging_bucket=aging_bucket,
            dunning_stage=dunning_stage,
            overdue=overdue,
            created_from=created_from,
            created_to=created_to,
            due_from=due_from,
            due_to=due_to,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order
        )



@router.post("/api/receivables/{id}/allocations", response_model=dict)
async def allocate_payment(
    payload: AllocatePaymentRequest,
    id: str = Path(..., min_length=1),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Allocates a payment to a receivable, utilizing pessimistic concurrency controls and idempotency."""
    alloc_ref = idempotency_key or payload.allocation_reference
    if not alloc_ref:
        raise HTTPException(status_code=400, detail="Idempotency-Key header or allocation_reference in body is required.")

    async with container.payment_allocation_repository() as alloc_repo:
        from ....domain.payment import PaymentAllocation, PaymentAllocationId
        from ....domain.payment.value_objects import PaymentId
        from ....domain.invoice.value_objects import Currency, CurrencyType, Money

        # Verify target Receivable exists and belongs to organization
        async with container.receivable_repository() as rec_repo:
            receivable = await rec_repo.get_by_id(ReceivableId(id))
            if not receivable or receivable.organization_id.value != ctx.organization_id:
                raise HTTPException(status_code=404, detail="Receivable not found.")

        # Ensure PaymentAllocation is initialized if not exists
        existing_alloc = await alloc_repo.find_by_id(PaymentAllocationId(alloc_ref))
        if not existing_alloc:
            currency_type = CurrencyType.DIGITAL_ASSET if payload.currency_code in ("USDC", "USDT") else CurrencyType.FIAT
            currency = Currency(payload.currency_code, currency_type)
            allocation = PaymentAllocation.create(
                id=PaymentAllocationId(alloc_ref),
                payment_id=PaymentId(payload.payment_id),
                invoice_id=receivable.invoice_id,
                organization_id=OrganizationId(ctx.organization_id),
                allocated_amount=Money(payload.allocated_amount, currency),
                status="ALLOCATED"
            ).value
            await alloc_repo.save(allocation)

    cmd = AllocatePaymentCommand(
        organization_id=ctx.organization_id,
        receivable_id=id,
        payment_id=payload.payment_id,
        allocated_amount=payload.allocated_amount,
        currency_code=payload.currency_code,
        allocation_reference=alloc_ref
    )

    async with container.allocate_payment_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return _receivable_to_dto(res.value)


@router.post("/api/receivables/{id}/overdue", response_model=dict)
async def mark_overdue(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Marks a receivable overdue."""
    cmd = MarkReceivableOverdueCommand(organization_id=ctx.organization_id, receivable_id=id)
    async with container.mark_receivable_overdue_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return _receivable_to_dto(res.value)


@router.post("/api/receivables/{id}/settle", response_model=dict)
async def settle_receivable(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Settles a receivable manually if outstanding balance is fully paid."""
    cmd = SettleReceivableCommand(organization_id=ctx.organization_id, receivable_id=id)
    async with container.settle_receivable_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return _receivable_to_dto(res.value)


@router.post("/api/receivables/{id}/write-off", response_model=dict)
async def write_off_receivable(
    payload: WriteOffReceivableRequest,
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Writes off a receivable aggregate."""
    cmd = WriteOffReceivableCommand(organization_id=ctx.organization_id, receivable_id=id, reason=payload.reason)
    async with container.write_off_receivable_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return _receivable_to_dto(res.value)


@router.get("/api/receivables/{id}/aging", response_model=dict)
async def get_receivable_aging(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves single receivable aging information."""
    async with container.receivable_query_service() as q_service:
        rec = await q_service.get_receivable(OrganizationId(ctx.organization_id), id)
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")
        
        due_date_dt = datetime.fromisoformat(rec["due_date"])
        now_dt = Clock.now()
        days_overdue = 0
        if now_dt > due_date_dt:
            days_overdue = (now_dt - due_date_dt).days
        
        return {
            "receivable_id": id,
            "current_aging_bucket": rec["aging_bucket"],
            "days_overdue": days_overdue,
            "previous_bucket": "CURRENT",
            "threshold_information": {},
            "timestamps": {
                "created_at": rec["created_at"],
                "updated_at": rec["updated_at"]
            }
        }


@router.get("/api/receivables/{id}/dunning", response_model=dict)
async def get_receivable_dunning(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves single receivable dunning status."""
    async with container.receivable_query_service() as q_service:
        rec = await q_service.get_receivable(OrganizationId(ctx.organization_id), id)
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")
        return {
            "receivable_id": id,
            "dunning_stage": rec["dunning_stage"],
            "dunning_attempt_count": rec["dunning_attempt_count"],
            "next_scheduled_action": rec["next_dunning_at"],
            "last_transition": rec["updated_at"],
            "collection_status": rec["collection_status"]
        }


@router.post("/api/receivables/{id}/dunning/advance", response_model=dict)
async def advance_dunning(
    payload: AdvanceDunningRequest,
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Advances dunning stage for a receivable."""
    cmd = AdvanceDunningStageCommand(
        organization_id=ctx.organization_id,
        receivable_id=id,
        next_stage=payload.next_stage,
        next_attempt_at=payload.next_attempt_at
    )
    async with container.advance_dunning_stage_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return _receivable_to_dto(res.value)


@router.get("/api/customers/{customer_reference}/receivables", response_model=dict)
@router.get("/api/customers/{customer_reference}/statement", response_model=dict)
async def get_customer_statement(
    customer_reference: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Generates a customer statement of account."""
    async with container.receivable_query_service() as q_service:
        receivables = await q_service.get_customer_receivables(
            OrganizationId(ctx.organization_id),
            customer_reference
        )

        total_receivables = Decimal("0.00")
        total_allocated = Decimal("0.00")
        total_outstanding = Decimal("0.00")
        overdue_amount = Decimal("0.00")
        settled_amount = Decimal("0.00")

        now_dt = Clock.now()
        for r in receivables:
            orig = Decimal(r["original_amount"])
            alloc = Decimal(r["allocated_amount"])
            out = Decimal(r["outstanding_amount"])

            total_receivables += orig
            total_allocated += alloc
            total_outstanding += out

            if r["status"] == "SETTLED" or r["payment_status"] == "PAID":
                settled_amount += orig

            due_dt = datetime.fromisoformat(r["due_date"])
            if now_dt > due_dt and (r["status"] != "SETTLED" and r["payment_status"] != "PAID"):
                overdue_amount += out

        return {
            "customer_reference": customer_reference,
            "total_receivables": f"{total_receivables:.2f}",
            "total_allocated": f"{total_allocated:.2f}",
            "total_outstanding": f"{total_outstanding:.2f}",
            "overdue_amount": f"{overdue_amount:.2f}",
            "settled_amount": f"{settled_amount:.2f}",
            "receivables": receivables
        }


from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import ReceivableDbModel, ReceivableCollectionActionDbModel, InvoiceDbModel, OutboxEventDbModel
from uuid import uuid4
import json
from sqlalchemy import func, select

@router.get("/api/receivables/collection/attention", response_model=List[dict])
async def get_collection_attention(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves list of receivables that need collection attention."""
    async with session_manager.session() as session:
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == ctx.organization_id,
            (ReceivableDbModel.status == "OVERDUE") | (ReceivableDbModel.collection_status == "COLLECTION_ATTENTION")
        )
        result = await session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "id": m.id,
                "reference": m.reference,
                "customer_reference": m.customer_reference,
                "invoice_reference": m.invoice_reference,
                "outstanding_amount": m.outstanding_amount,
                "currency": m.currency,
                "status": m.status,
                "collection_status": m.collection_status,
                "dunning_stage": m.dunning_stage,
                "due_date": m.due_date.isoformat()
            }
            for m in models
        ]


@router.get("/api/receivables/dunning/overdue", response_model=List[dict])
async def get_dunning_overdue_list(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves list of overdue receivables."""
    async with session_manager.session() as session:
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == ctx.organization_id,
            ReceivableDbModel.status == "OVERDUE"
        )
        result = await session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "id": m.id,
                "reference": m.reference,
                "customer_reference": m.customer_reference,
                "invoice_reference": m.invoice_reference,
                "outstanding_amount": m.outstanding_amount,
                "currency": m.currency,
                "status": m.status,
                "dunning_stage": m.dunning_stage,
                "due_date": m.due_date.isoformat()
            }
            for m in models
        ]


@router.get("/api/receivables/collection/failures", response_model=List[dict])
async def get_collection_failures(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves list of failed collection actions."""
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id,
            ReceivableCollectionActionDbModel.status == "FAILED"
        )
        result = await session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "id": m.id,
                "receivable_id": m.receivable_id,
                "action_type": m.action_type,
                "scheduled_at": m.scheduled_at.isoformat(),
                "status": m.status,
                "attempt_count": m.attempt_count,
                "error_message": m.error_message
            }
            for m in models
        ]


@router.get("/api/receivables/collection/stats", response_model=dict)
async def get_collection_stats(
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves operational stats for collections."""
    async with session_manager.session() as session:
        # Overdue count and amount
        overdue_stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == ctx.organization_id,
            ReceivableDbModel.status == "OVERDUE"
        )
        overdue_res = await session.execute(overdue_stmt)
        overdue_models = overdue_res.scalars().all()
        
        total_overdue_count = len(overdue_models)
        total_overdue_amount = sum(Decimal(m.outstanding_amount) for m in overdue_models)

        # Aging bucket groupings
        aging_stmt = select(ReceivableDbModel.aging_bucket, func.count(ReceivableDbModel.id)).where(
            ReceivableDbModel.organization_id == ctx.organization_id
        ).group_by(ReceivableDbModel.aging_bucket)
        aging_res = await session.execute(aging_stmt)
        aging_buckets = {row[0]: row[1] for row in aging_res.all()}

        # Dunning stage groupings
        dunning_stmt = select(ReceivableDbModel.dunning_stage, func.count(ReceivableDbModel.id)).where(
            ReceivableDbModel.organization_id == ctx.organization_id
        ).group_by(ReceivableDbModel.dunning_stage)
        dunning_res = await session.execute(dunning_stmt)
        dunning_stages = {row[0]: row[1] for row in dunning_res.all()}

        # Action attempts/success/failure counts
        actions_stmt = select(ReceivableCollectionActionDbModel.status, func.count(ReceivableCollectionActionDbModel.id)).where(
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id
        ).group_by(ReceivableCollectionActionDbModel.status)
        actions_res = await session.execute(actions_stmt)
        action_stats = {row[0]: row[1] for row in actions_res.all()}

        # Amount recovered
        rec_stmt = select(ReceivableDbModel.allocated_amount).where(
            ReceivableDbModel.organization_id == ctx.organization_id
        )
        rec_res = await session.execute(rec_stmt)
        amount_recovered = sum(Decimal(val) for val in rec_res.scalars().all())

        # Amount written off
        wo_stmt = select(ReceivableDbModel.original_amount).where(
            ReceivableDbModel.organization_id == ctx.organization_id,
            ReceivableDbModel.status == "WRITTEN_OFF"
        )
        wo_res = await session.execute(wo_stmt)
        amount_written_off = sum(Decimal(val) for val in wo_res.scalars().all())

        # Average days to settlement
        settled_stmt = select(ReceivableDbModel.created_at, ReceivableDbModel.updated_at).where(
            ReceivableDbModel.organization_id == ctx.organization_id,
            ReceivableDbModel.status == "SETTLED"
        )
        settled_res = await session.execute(settled_stmt)
        settled_times = settled_res.all()
        
        if settled_times:
            total_days = sum((row[1] - row[0]).days for row in settled_times)
            avg_days = total_days / len(settled_times)
        else:
            avg_days = 0.0

        return {
            "total_overdue_count": total_overdue_count,
            "total_overdue_amount": f"{total_overdue_amount:.2f}",
            "aging_buckets": aging_buckets,
            "dunning_stages": dunning_stages,
            "action_stats": action_stats,
            "amount_recovered": f"{amount_recovered:.2f}",
            "amount_written_off": f"{amount_written_off:.2f}",
            "average_days_to_settlement": avg_days
        }


@router.post("/api/receivables/{id}/dunning/retry")
async def retry_dunning_action(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Retries failed collection actions for a receivable."""
    async with session_manager.session() as session:
        # Verify receivable exists and belongs to organization
        rec_stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.id == id,
            ReceivableDbModel.organization_id == ctx.organization_id
        )
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")

        # Find failed actions and reset
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == id,
            ReceivableCollectionActionDbModel.status == "FAILED",
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id
        )
        result = await session.execute(stmt)
        actions = result.scalars().all()
        
        for action in actions:
            action.status = "PENDING"
            action.attempt_count = 0
            action.next_retry_at = Clock.now()
            action.error_message = None

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="ReceivableDunningActionRetried",
            payload=json.dumps({
                "receivable_id": id,
                "organization_id": ctx.organization_id,
                "retry_count": len(actions),
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "retried_count": len(actions)}


@router.post("/api/receivables/{id}/dunning/replay")
async def replay_dunning_action(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Replays/resends the dunning reminder action immediately."""
    async with session_manager.session() as session:
        rec_stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.id == id,
            ReceivableDbModel.organization_id == ctx.organization_id
        )
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")

        # Schedule new action
        action_id = str(uuid4())
        # Determine rule action type
        from ....application.receivable.dunning_policy import DunningPolicy
        from ....domain.receivable.dunning import DunningStage
        policy = DunningPolicy()
        rule = policy.get_rule(DunningStage(rec.dunning_stage))
        action_type = rule.action_type if rule else "SEND_REMINDER"

        new_action = ReceivableCollectionActionDbModel(
            id=action_id,
            organization_id=ctx.organization_id,
            receivable_id=id,
            action_type=action_type,
            scheduled_at=Clock.now(),
            status="PENDING",
            attempt_count=0,
            idempotency_key=f"manual-replay-{action_id}",
            created_at=Clock.now()
        )
        session.add(new_action)

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="ReceivableDunningActionReplayed",
            payload=json.dumps({
                "receivable_id": id,
                "organization_id": ctx.organization_id,
                "action_type": action_type,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "action_id": action_id}


@router.post("/api/receivables/{id}/dunning/pause")
async def pause_dunning(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Pauses automated dunning progression for a receivable."""
    async with session_manager.session() as session:
        rec_stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.id == id,
            ReceivableDbModel.organization_id == ctx.organization_id
        ).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")

        # Explicitly pause dunning
        rec.dunning_paused = True
        rec.next_dunning_at = datetime(9999, 12, 31)

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="DunningPaused",
            payload=json.dumps({
                "receivable_id": id,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Dunning paused successfully."}


@router.post("/api/receivables/{id}/dunning/resume")
async def resume_dunning(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Resumes paused automated dunning progression for a receivable."""
    async with session_manager.session() as session:
        rec_stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.id == id,
            ReceivableDbModel.organization_id == ctx.organization_id
        ).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")

        # Explicitly resume dunning
        rec.dunning_paused = False
        # Reset next dunning trigger to now
        rec.next_dunning_at = Clock.now()

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="DunningResumed",
            payload=json.dumps({
                "receivable_id": id,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Dunning resumed successfully."}


from ....application.receivable.collection_action_query_service import ICollectionActionQueryService
from ....infrastructure.database.collection_action_query_service import PostgresCollectionActionQueryService

@router.get("/api/receivables/collection-actions/failed")
async def get_failed_collection_actions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER", "MEMBER"]))
):
    """Retrieves list of failed/retryable collection actions."""
    async with session_manager.session() as session:
        query_service = PostgresCollectionActionQueryService(session)
        org_id = OrganizationId(ctx.organization_id)
        actions = await query_service.get_failed_actions(org_id, limit, offset)
        return {"status": "success", "data": actions}


@router.get("/api/receivables/collection-actions/dead-letter")
async def get_dead_letter_collection_actions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER", "MEMBER"]))
):
    """Retrieves list of dead-lettered collection actions."""
    async with session_manager.session() as session:
        query_service = PostgresCollectionActionQueryService(session)
        org_id = OrganizationId(ctx.organization_id)
        actions = await query_service.get_dead_letter_actions(org_id, limit, offset)
        return {"status": "success", "data": actions}


@router.get("/api/receivables/collection-actions/metrics")
async def get_collection_action_metrics(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER", "MEMBER"]))
):
    """Retrieves metrics overview for collection actions."""
    async with session_manager.session() as session:
        query_service = PostgresCollectionActionQueryService(session)
        org_id = OrganizationId(ctx.organization_id)
        metrics = await query_service.get_collection_action_metrics(org_id)
        return {"status": "success", "data": metrics}


@router.post("/api/receivables/collection-actions/{id}/retry")
async def retry_collection_action_by_id(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Retries a specific failed/dead-lettered collection action."""
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.id == id,
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id
        ).with_for_update()
        action = (await session.execute(stmt)).scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=404, detail="Collection action not found.")

        # Reset action status to retry
        action.status = "PENDING"
        action.attempt_count = 0
        action.next_retry_at = Clock.now()
        action.error_message = None

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="CollectionActionRetried",
            payload=json.dumps({
                "action_id": id,
                "receivable_id": action.receivable_id,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Collection action reset for retry."}


@router.post("/api/receivables/collection-actions/{id}/replay")
async def replay_collection_action_by_id(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Replays/resends a specific collection action immediately."""
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.id == id,
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id
        ).with_for_update()
        action = (await session.execute(stmt)).scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=404, detail="Collection action not found.")

        # Force replay by resetting to PENDING and now
        action.status = "PENDING"
        action.attempt_count = 0
        action.scheduled_at = Clock.now()
        action.next_retry_at = None
        action.error_message = None

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="CollectionActionReplayed",
            payload=json.dumps({
                "action_id": id,
                "receivable_id": action.receivable_id,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Collection action scheduled for replay."}


@router.post("/api/receivables/collection-actions/{id}/cancel")
async def cancel_collection_action(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Cancels a scheduled collection action."""
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.id == id,
            ReceivableCollectionActionDbModel.organization_id == ctx.organization_id
        ).with_for_update()
        action = (await session.execute(stmt)).scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=404, detail="Collection action not found.")

        action.status = "CANCELLED"

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="CollectionActionCancelled",
            payload=json.dumps({
                "action_id": id,
                "receivable_id": action.receivable_id,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Collection action cancelled successfully."}


@router.post("/api/receivables/payment-events/{id}/retry")
async def retry_payment_event(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Retries a failed outbox payment event."""
    async with session_manager.session() as session:
        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.id == id
        ).with_for_update()
        event = (await session.execute(stmt)).scalar_one_or_none()
        if not event:
            raise HTTPException(status_code=404, detail="Payment outbox event not found.")

        # Reset event to retry
        event.status = "PENDING"
        event.processed = False
        event.attempts = 0
        event.next_retry_at = Clock.now()
        event.error_message = None

        await session.commit()
        return {"status": "success", "message": "Payment outbox event reset for retry."}


@router.post("/api/receivables/payment-events/{id}/replay")
async def replay_payment_event(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Replays a processed or failed payment event."""
    async with session_manager.session() as session:
        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.id == id
        ).with_for_update()
        event = (await session.execute(stmt)).scalar_one_or_none()
        if not event:
            raise HTTPException(status_code=404, detail="Payment outbox event not found.")

        # Force replay
        event.status = "PENDING"
        event.processed = False
        event.attempts = 0
        event.next_retry_at = Clock.now()
        event.error_message = None

        # Emit audit log event to outbox
        audit_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentEventReplayed",
            payload=json.dumps({
                "payment_event_id": id,
                "event_type": event.event_type,
                "organization_id": ctx.organization_id,
                "occurred_on": Clock.now().isoformat()
            }),
            processed=False,
            created_at=Clock.now()
        )
        session.add(audit_event)
        await session.commit()
        return {"status": "success", "message": "Payment outbox event scheduled for replay."}


@router.get("/api/receivables/metrics", response_model=dict)
async def get_ar_metrics(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Retrieves collection engine and receivables operational metrics."""
    async with container.receivable_query_service() as q_service:
        return await q_service.get_ar_metrics(OrganizationId(ctx.organization_id))


@router.get("/api/receivables/health", response_model=dict)
async def get_ar_health(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Calculates operational health diagnostics for the collection engine."""
    async with container.receivable_query_service() as q_service:
        return await q_service.get_ar_health(OrganizationId(ctx.organization_id))


@router.get("/api/receivables/audit", response_model=List[dict])
async def get_ar_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Queries AR-related audit trail logs with actor and correlation details."""
    async with container.receivable_query_service() as q_service:
        return await q_service.get_ar_audit_logs(
            organization_id=OrganizationId(ctx.organization_id),
            limit=limit,
            offset=offset
        )


@router.get("/api/receivables/{id}", response_model=dict)
async def get_receivable_by_id(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves a single receivable by ID if it belongs to the caller's tenant context."""
    async with container.receivable_query_service() as q_service:
        rec = await q_service.get_receivable(OrganizationId(ctx.organization_id), id)
        if not rec:
            raise HTTPException(status_code=404, detail="Receivable not found.")
        return rec


@router.get("/api/receivables/{id}/timeline", response_model=List[dict])
async def get_receivable_timeline(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Generates a chronological timeline of all operations on a single receivable."""
    # Strict tenant ownership boundary validation
    async with container.receivable_repository() as repo:
        receivable = await repo.get_by_id(ReceivableId(id))
        if not receivable or receivable.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receivable not found.")

    async with container.receivable_query_service() as q_service:
        return await q_service.get_receivable_timeline(
            organization_id=OrganizationId(ctx.organization_id),
            receivable_id=id
        )


@router.get("/api/receivables/{id}/collection-history", response_model=List[dict])
async def get_collection_history(
    id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves full historical attempt records of collection actions on a receivable."""
    # Strict tenant ownership boundary validation
    async with container.receivable_repository() as repo:
        receivable = await repo.get_by_id(ReceivableId(id))
        if not receivable or receivable.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receivable not found.")

    async with container.receivable_query_service() as q_service:
        return await q_service.get_collection_history(
            organization_id=OrganizationId(ctx.organization_id),
            receivable_id=id
        )


