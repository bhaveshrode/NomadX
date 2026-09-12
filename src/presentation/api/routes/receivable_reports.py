from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Path, Query, status
from pydantic import BaseModel

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.shared.clock.clock import Clock
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import InvoiceDbModel

router = APIRouter(tags=["receivable_reports"])


async def _verify_customer_exists(customer_id: str, organization_id: str) -> None:
    """Verifies that the customer has at least one invoice in this organization to prevent tenant leaks."""
    async with session_manager.session() as session:
        stmt = select(func.count(InvoiceDbModel.id)).where(
            InvoiceDbModel.organization_id == organization_id,
            InvoiceDbModel.customer_id == customer_id
        )
        res = await session.execute(stmt)
        count = res.scalar_one() or 0
        if count == 0:
            # Query from Receivables as well to be sure
            from ....infrastructure.database.models import ReceivableDbModel
            rec_stmt = select(func.count(ReceivableDbModel.id)).where(
                ReceivableDbModel.organization_id == organization_id,
                ReceivableDbModel.customer_id == customer_id
            )
            rec_res = await session.execute(rec_stmt)
            rec_count = rec_res.scalar_one() or 0
            if rec_count == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")


from sqlalchemy import select, func


@router.get("/api/customers/{customer_id}/receivables/summary", response_model=dict)
async def get_customer_receivables_summary(
    customer_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Retrieves high-level AR summary metrics for a specific customer."""
    await _verify_customer_exists(customer_id, ctx.organization_id)

    async with container.receivable_query_service() as query_service:
        summary = await query_service.get_customer_ar_summary(
            OrganizationId(ctx.organization_id),
            customer_id,
            Clock.now()
        )
        return summary


@router.get("/api/customers/{customer_id}/statement", response_model=dict)
async def get_customer_statement(
    customer_id: str = Path(..., min_length=1),
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Generates a customer statement of account for a specific period [from, to)."""
    await _verify_customer_exists(customer_id, ctx.organization_id)

    try:
        period_start = datetime.fromisoformat(from_date)
        period_end = datetime.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use ISO format (e.g. YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)."
        )

    async with container.receivable_query_service() as query_service:
        statement = await query_service.get_customer_statement(
            OrganizationId(ctx.organization_id),
            customer_id,
            period_start,
            period_end,
            limit,
            offset
        )
        return statement


@router.get("/api/customers/{customer_id}/receivables/aging", response_model=dict)
async def get_customer_aging(
    customer_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Retrieves aging bucket breakdown for a specific customer."""
    await _verify_customer_exists(customer_id, ctx.organization_id)

    async with container.receivable_query_service() as query_service:
        aging = await query_service.get_customer_aging(
            OrganizationId(ctx.organization_id),
            customer_id,
            Clock.now()
        )
        return aging


@router.get("/api/receivables/summary", response_model=dict)
async def get_org_receivables_summary(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Retrieves organization-wide AR dashboard summary metrics."""
    async with container.receivable_query_service() as query_service:
        summary = await query_service.get_org_ar_summary(
            OrganizationId(ctx.organization_id),
            Clock.now()
        )
        return summary


@router.get("/api/receivables/outstanding", response_model=List[dict])
async def get_outstanding_receivables(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Retrieves a list of all outstanding invoices/receivables with balances."""
    async with container.receivable_query_service() as query_service:
        receivables = await query_service.get_outstanding_receivables(
            OrganizationId(ctx.organization_id),
            limit,
            offset
        )
        return receivables
