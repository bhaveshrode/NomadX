from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, cast, Numeric, case
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...domain.shared.clock.clock import Clock
from ...domain.receivable.value_objects import AgingBucket
from ...application.receivable.query_service import IReceivableQueryService
import json
from ...application.notification.query_service import INotificationQueryService
from ...application.workflow.query_service import IWorkflowQueryService
from .models import (
    ReceivableDbModel,
    ReceivableAllocationDbModel,
    InvoiceDbModel,
    PaymentAllocationDbModel,
    PaymentDbModel,
    NotificationDbModel,
    NotificationDeliveryAttemptDbModel,
    ProviderStateDbModel,
    WorkflowDbModel,
    WorkflowVersionDbModel,
    WorkflowExecutionDbModel,
    OutboxEventDbModel,
    ReceivableCollectionActionDbModel
)


class PostgresReceivableQueryService(IReceivableQueryService):
    """
    SQLAlchemy/PostgreSQL implementation of read-only Accounts Receivable Reporting Queries.
    Optimized for single-query aggregations and timezone/fiat rounding.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_customer_ar_summary(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        current_time: datetime
    ) -> Dict[str, Any]:
        t_current = current_time
        t_30 = current_time - timedelta(days=30)

        stmt = select(
            func.coalesce(func.sum(cast(ReceivableDbModel.original_amount, Numeric)), 0),
            func.coalesce(func.sum(cast(ReceivableDbModel.paid_amount, Numeric)), 0),
            func.coalesce(func.sum(cast(ReceivableDbModel.outstanding_amount, Numeric)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date >= t_current, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_current) & (ReceivableDbModel.due_date >= t_30), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date < t_30, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.customer_id == customer_id
        )

        res = await self._session.execute(stmt)
        row = res.fetchone()
        
        orig, paid, outstanding, current, overdue, attention = row if row else (0, 0, 0, 0, 0, 0)

        return {
            "total_invoiced": f"{Decimal(str(orig)):.2f}",
            "total_paid": f"{Decimal(str(paid)):.2f}",
            "total_outstanding": f"{Decimal(str(outstanding)):.2f}",
            "current": f"{Decimal(str(current)):.2f}",
            "overdue": f"{Decimal(str(overdue)):.2f}",
            "collection_attention": f"{Decimal(str(attention)):.2f}"
        }

    async def get_customer_statement(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        period_start: datetime,
        period_end: datetime,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        # 1. Opening Balance Calculation
        stmt_inv_before = select(func.coalesce(func.sum(cast(InvoiceDbModel.grand_total, Numeric)), 0)).where(
            InvoiceDbModel.organization_id == organization_id.value,
            InvoiceDbModel.customer_id == customer_id,
            InvoiceDbModel.issued_at < period_start
        )
        res_inv_before = await self._session.execute(stmt_inv_before)
        invs_before = res_inv_before.scalar_one() or Decimal("0.00")

        stmt_alloc_before = select(func.coalesce(func.sum(cast(PaymentAllocationDbModel.allocated_amount, Numeric)), 0)).join(
            InvoiceDbModel,
            PaymentAllocationDbModel.invoice_id == InvoiceDbModel.id
        ).where(
            PaymentAllocationDbModel.organization_id == organization_id.value,
            InvoiceDbModel.customer_id == customer_id,
            PaymentAllocationDbModel.created_at < period_start
        )
        res_alloc_before = await self._session.execute(stmt_alloc_before)
        allocs_before = res_alloc_before.scalar_one() or Decimal("0.00")

        opening_balance = Decimal(str(invs_before)) - Decimal(str(allocs_before))

        # 2. Retrieve Invoice Activity (Debits)
        stmt_invoices = select(InvoiceDbModel).where(
            InvoiceDbModel.organization_id == organization_id.value,
            InvoiceDbModel.customer_id == customer_id,
            InvoiceDbModel.issued_at >= period_start,
            InvoiceDbModel.issued_at < period_end
        )
        res_invoices = await self._session.execute(stmt_invoices)
        invoices = res_invoices.scalars().all()

        # 3. Retrieve Payment Allocation Activity (Credits)
        stmt_allocations = select(
            PaymentAllocationDbModel,
            PaymentDbModel.transaction_reference,
            InvoiceDbModel.invoice_number
        ).join(
            InvoiceDbModel,
            PaymentAllocationDbModel.invoice_id == InvoiceDbModel.id
        ).join(
            PaymentDbModel,
            PaymentAllocationDbModel.payment_id == PaymentDbModel.id
        ).where(
            PaymentAllocationDbModel.organization_id == organization_id.value,
            InvoiceDbModel.customer_id == customer_id,
            PaymentAllocationDbModel.created_at >= period_start,
            PaymentAllocationDbModel.created_at < period_end
        )
        res_allocs = await self._session.execute(stmt_allocations)
        allocations = res_allocs.all()

        # 4. Construct ledger entries
        entries = []
        invoices_total = Decimal("0.00")
        payments_total = Decimal("0.00")

        for inv in invoices:
            amt = Decimal(inv.grand_total)
            invoices_total += amt
            entries.append({
                "date": inv.issued_at,
                "type": "INVOICE",
                "reference": inv.invoice_number,
                "description": f"Invoice issued: {inv.invoice_number}",
                "debit": amt,
                "credit": Decimal("0.00"),
                "id": inv.id
            })

        for alloc_row in allocations:
            alloc_model = alloc_row[0]
            tx_ref = alloc_row[1]
            inv_num = alloc_row[2]

            amt = Decimal(alloc_model.allocated_amount)
            payments_total += amt
            entries.append({
                "date": alloc_model.created_at,
                "type": "PAYMENT",
                "reference": alloc_model.payment_id,
                "description": f"Payment allocation ({tx_ref}) to invoice {inv_num}",
                "debit": Decimal("0.00"),
                "credit": amt,
                "id": alloc_model.id
            })

        # Sort chronologically by date, and secondarily by ID
        entries.sort(key=lambda x: (x["date"], x["id"]))

        # Compute running balances across all sorted entries
        running = opening_balance
        for entry in entries:
            running = running + entry["debit"] - entry["credit"]
            entry["running_balance"] = f"{running:.2f}"
            entry["debit"] = f"{entry['debit']:.2f}"
            entry["credit"] = f"{entry['credit']:.2f}"
            entry["date"] = entry["date"].isoformat()

        closing_balance = running

        # Paginate
        paginated_entries = entries[offset : offset + limit]

        # 5. Reconciliation Invariant Check
        stmt_rec_total = select(func.coalesce(func.sum(cast(ReceivableDbModel.original_amount, Numeric)), 0)).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.customer_id == customer_id
        )
        res_rec_total = await self._session.execute(stmt_rec_total)
        rec_total = res_rec_total.scalar_one() or Decimal("0.00")

        stmt_alloc_total = select(func.coalesce(func.sum(cast(PaymentAllocationDbModel.allocated_amount, Numeric)), 0)).join(
            InvoiceDbModel,
            PaymentAllocationDbModel.invoice_id == InvoiceDbModel.id
        ).where(
            PaymentAllocationDbModel.organization_id == organization_id.value,
            InvoiceDbModel.customer_id == customer_id,
            PaymentAllocationDbModel.created_at < period_end
        )
        res_alloc_total = await self._session.execute(stmt_alloc_total)
        alloc_total = res_alloc_total.scalar_one() or Decimal("0.00")

        outstanding_ar_at_end = Decimal(str(rec_total)) - Decimal(str(alloc_total))
        reconciled = (closing_balance == outstanding_ar_at_end)

        return {
            "organization_id": organization_id.value,
            "customer_id": customer_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "opening_balance": f"{opening_balance:.2f}",
            "invoices_total": f"{invoices_total:.2f}",
            "payments_total": f"{payments_total:.2f}",
            "closing_balance": f"{closing_balance:.2f}",
            "reconciled": reconciled,
            "entries": paginated_entries
        }

    async def get_customer_aging(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        current_time: datetime
    ) -> Dict[str, Any]:
        t_current = current_time
        t_30 = current_time - timedelta(days=30)
        t_60 = current_time - timedelta(days=60)
        t_90 = current_time - timedelta(days=90)

        stmt = select(
            func.coalesce(func.sum(case((ReceivableDbModel.due_date >= t_current, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_current) & (ReceivableDbModel.due_date >= t_30), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_30) & (ReceivableDbModel.due_date >= t_60), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_60) & (ReceivableDbModel.due_date >= t_90), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date < t_90, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.customer_id == customer_id,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        )

        res = await self._session.execute(stmt)
        row = res.fetchone()

        c, d1, d2, d3, d4 = row if row else (0, 0, 0, 0, 0)
        c = Decimal(str(c)).quantize(Decimal("0.00"))
        d1 = Decimal(str(d1)).quantize(Decimal("0.00"))
        d2 = Decimal(str(d2)).quantize(Decimal("0.00"))
        d3 = Decimal(str(d3)).quantize(Decimal("0.00"))
        d4 = Decimal(str(d4)).quantize(Decimal("0.00"))
        total = c + d1 + d2 + d3 + d4

        return {
            "current": f"{c:.2f}",
            "days_1_30": f"{d1:.2f}",
            "days_31_60": f"{d2:.2f}",
            "days_61_90": f"{d3:.2f}",
            "days_90_plus": f"{d4:.2f}",
            "total_outstanding": f"{total:.2f}"
        }

    async def get_org_ar_summary(
        self,
        organization_id: OrganizationId,
        current_time: datetime
    ) -> Dict[str, Any]:
        t_current = current_time
        t_30 = current_time - timedelta(days=30)

        # 1. Aging aggregates
        stmt1 = select(
            func.coalesce(func.sum(cast(ReceivableDbModel.outstanding_amount, Numeric)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date >= t_current, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_current) & (ReceivableDbModel.due_date >= t_30), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date < t_30, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0)
        ).where(ReceivableDbModel.organization_id == organization_id.value)
        
        res1 = await self._session.execute(stmt1)
        row = res1.fetchone()
        outstanding, current, overdue, attention = row if row else (0, 0, 0, 0)

        # 2. Count of outstanding invoices
        stmt2 = select(func.count(ReceivableDbModel.id)).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        )
        res2 = await self._session.execute(stmt2)
        invoices_count = res2.scalar_one() or 0

        # 3. Count of customers with balance
        stmt3 = select(func.count(ReceivableDbModel.customer_id.distinct())).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        )
        res3 = await self._session.execute(stmt3)
        customers_with_balance = res3.scalar_one() or 0

        # 4. Count of overdue customers
        stmt4 = select(func.count(ReceivableDbModel.customer_id.distinct())).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0,
            ReceivableDbModel.due_date < current_time
        )
        res4 = await self._session.execute(stmt4)
        overdue_customers = res4.scalar_one() or 0

        return {
            "total_outstanding": f"{Decimal(str(outstanding)):.2f}",
            "current": f"{Decimal(str(current)):.2f}",
            "overdue": f"{Decimal(str(overdue)):.2f}",
            "collection_attention": f"{Decimal(str(attention)):.2f}",
            "invoices_outstanding": invoices_count,
            "customers_with_balance": customers_with_balance,
            "overdue_customers": overdue_customers
        }

    async def get_outstanding_receivables(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = select(
            ReceivableDbModel,
            InvoiceDbModel.invoice_number,
            InvoiceDbModel.issued_at
        ).join(
            InvoiceDbModel,
            ReceivableDbModel.invoice_id == InvoiceDbModel.id
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        ).order_by(
            ReceivableDbModel.due_date.asc(),
            ReceivableDbModel.id.asc()
        ).limit(limit).offset(offset)

        res = await self._session.execute(stmt)
        rows = res.all()

        results = []
        now_dt = Clock.now() # fallback, but dynamically calculated

        for row in rows:
            rec_model = row[0]
            inv_num = row[1]
            issued_at = row[2]
            
            due_dt = rec_model.due_date
            days_overdue = 0
            if now_dt > due_dt:
                days_overdue = (now_dt - due_dt).days
            aging_b = AgingBucket.from_days_overdue(days_overdue).value

            results.append({
                "invoice_number": inv_num,
                "issued_date": issued_at.isoformat() if issued_at else None,
                "due_date": due_dt.isoformat(),
                "original_amount": f"{Decimal(rec_model.original_amount):.2f}",
                "paid_amount": f"{Decimal(rec_model.paid_amount):.2f}",
                "outstanding_amount": f"{Decimal(rec_model.outstanding_amount):.2f}",
                "payment_status": rec_model.payment_status,
                "collection_status": rec_model.collection_status,
                "aging_bucket": aging_b
            })

        return results

    async def get_receivable(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> Optional[Dict[str, Any]]:
        # Fetch receivable
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.id == receivable_id
        )
        res = await self._session.execute(stmt)
        m = res.scalar_one_or_none()
        if not m:
            return None
        
        # Load allocations
        alloc_stmt = select(ReceivableAllocationDbModel).where(
            ReceivableAllocationDbModel.receivable_id == m.id
        ).order_by(ReceivableAllocationDbModel.allocated_at.asc())
        alloc_res = await self._session.execute(alloc_stmt)
        allocs = alloc_res.scalars().all()

        return {
            "id": m.id,
            "organization_id": m.organization_id,
            "customer_id": m.customer_id,
            "invoice_id": m.invoice_id,
            "reference": m.reference,
            "customer_reference": m.customer_reference,
            "invoice_reference": m.invoice_reference,
            "currency": m.currency,
            "original_amount": f"{Decimal(m.original_amount):.2f}",
            "allocated_amount": f"{Decimal(m.allocated_amount):.2f}",
            "outstanding_amount": f"{Decimal(m.outstanding_amount):.2f}",
            "due_date": m.due_date.isoformat(),
            "status": m.status,
            "aging_bucket": m.aging_bucket,
            "dunning_stage": m.dunning_stage,
            "dunning_attempt_count": m.dunning_attempt_count,
            "next_dunning_at": m.next_dunning_at.isoformat() if m.next_dunning_at else None,
            "paid_amount": f"{Decimal(m.paid_amount):.2f}",
            "payment_status": m.payment_status,
            "collection_status": m.collection_status,
            "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat(),
            "allocations": [
                {
                    "id": a.id,
                    "payment_reference": a.payment_reference,
                    "allocated_amount": f"{Decimal(a.allocated_amount):.2f}",
                    "currency": a.currency,
                    "allocated_at": a.allocated_at.isoformat()
                } for a in allocs
            ]
        }

    async def list_receivables(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        customer_reference: Optional[str] = None,
        invoice_reference: Optional[str] = None,
        aging_bucket: Optional[str] = None,
        dunning_stage: Optional[str] = None,
        overdue: Optional[bool] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        due_from: Optional[datetime] = None,
        due_to: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value
        )
        if status:
            stmt = stmt.where(ReceivableDbModel.status == status)
        if customer_reference:
            stmt = stmt.where(ReceivableDbModel.customer_reference == customer_reference)
        if invoice_reference:
            stmt = stmt.where(ReceivableDbModel.invoice_reference == invoice_reference)
        if aging_bucket:
            stmt = stmt.where(ReceivableDbModel.aging_bucket == aging_bucket)
        if dunning_stage:
            stmt = stmt.where(ReceivableDbModel.dunning_stage == dunning_stage)
        if overdue is not None:
            if overdue:
                stmt = stmt.where((ReceivableDbModel.status == "OVERDUE") | (ReceivableDbModel.due_date < Clock.now()))
            else:
                stmt = stmt.where((ReceivableDbModel.status != "OVERDUE") & (ReceivableDbModel.due_date >= Clock.now()))
        if created_from:
            stmt = stmt.where(ReceivableDbModel.created_at >= created_from)
        if created_to:
            stmt = stmt.where(ReceivableDbModel.created_at <= created_to)
        if due_from:
            stmt = stmt.where(ReceivableDbModel.due_date >= due_from)
        if due_to:
            stmt = stmt.where(ReceivableDbModel.due_date <= due_to)

        # Sorting
        if sort_by:
            col = getattr(ReceivableDbModel, sort_by, None)
            if col is not None:
                if sort_order == "desc":
                    stmt = stmt.order_by(col.desc())
                else:
                    stmt = stmt.order_by(col.asc())
        else:
            stmt = stmt.order_by(ReceivableDbModel.created_at.desc())

        stmt = stmt.limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        models = res.scalars().all()

        return [
            {
                "id": m.id,
                "organization_id": m.organization_id,
                "customer_id": m.customer_id,
                "invoice_id": m.invoice_id,
                "reference": m.reference,
                "customer_reference": m.customer_reference,
                "invoice_reference": m.invoice_reference,
                "currency": m.currency,
                "original_amount": f"{Decimal(m.original_amount):.2f}",
                "allocated_amount": f"{Decimal(m.allocated_amount):.2f}",
                "outstanding_amount": f"{Decimal(m.outstanding_amount):.2f}",
                "due_date": m.due_date.isoformat(),
                "status": m.status,
                "aging_bucket": m.aging_bucket,
                "dunning_stage": m.dunning_stage,
                "dunning_attempt_count": m.dunning_attempt_count,
                "next_dunning_at": m.next_dunning_at.isoformat() if m.next_dunning_at else None,
                "paid_amount": f"{Decimal(m.paid_amount):.2f}",
                "payment_status": m.payment_status,
                "collection_status": m.collection_status,
                "created_at": m.created_at.isoformat(),
                "updated_at": m.updated_at.isoformat()
            } for m in models
        ]

    async def get_customer_receivables(
        self,
        organization_id: OrganizationId,
        customer_reference: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        return await self.list_receivables(
            organization_id=organization_id,
            customer_reference=customer_reference,
            limit=limit,
            offset=offset
        )

    async def get_allocations(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        stmt = select(ReceivableAllocationDbModel).where(
            ReceivableAllocationDbModel.organization_id == organization_id.value,
            ReceivableAllocationDbModel.receivable_id == receivable_id
        ).order_by(ReceivableAllocationDbModel.allocated_at.desc())
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "id": m.id,
                "receivable_id": m.receivable_id,
                "payment_reference": m.payment_reference,
                "allocated_amount": f"{Decimal(m.allocated_amount):.2f}",
                "currency": m.currency,
                "allocated_at": m.allocated_at.isoformat()
            } for m in models
        ]

    async def get_receivable_statement(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> Dict[str, Any]:
        rec = await self.get_receivable(organization_id, receivable_id)
        if not rec:
            return {}
        return {
            "receivable": rec,
            "allocations": rec.get("allocations", [])
        }

    async def get_aging_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        t_current = Clock.now()
        t_30 = t_current - timedelta(days=30)
        t_60 = t_current - timedelta(days=60)
        t_90 = t_current - timedelta(days=90)

        stmt = select(
            func.coalesce(func.sum(case((ReceivableDbModel.due_date >= t_current, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_current) & (ReceivableDbModel.due_date >= t_30), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_30) & (ReceivableDbModel.due_date >= t_60), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_60) & (ReceivableDbModel.due_date >= t_90), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date < t_90, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        )

        res = await self._session.execute(stmt)
        row = res.fetchone()

        c, d1, d2, d3, d4 = row if row else (0, 0, 0, 0, 0)
        c = Decimal(str(c)).quantize(Decimal("0.00"))
        d1 = Decimal(str(d1)).quantize(Decimal("0.00"))
        d2 = Decimal(str(d2)).quantize(Decimal("0.00"))
        d3 = Decimal(str(d3)).quantize(Decimal("0.00"))
        d4 = Decimal(str(d4)).quantize(Decimal("0.00"))
        total = c + d1 + d2 + d3 + d4

        return {
            "current": f"{c:.2f}",
            "days_1_30": f"{d1:.2f}",
            "days_31_60": f"{d2:.2f}",
            "days_61_90": f"{d3:.2f}",
            "days_90_plus": f"{d4:.2f}",
            "total_outstanding": f"{total:.2f}"
        }

    async def get_dunning_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        stmt = select(
            ReceivableDbModel.dunning_stage,
            func.count(ReceivableDbModel.id),
            func.coalesce(func.sum(cast(ReceivableDbModel.outstanding_amount, Numeric)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        ).group_by(ReceivableDbModel.dunning_stage)

        res = await self._session.execute(stmt)
        rows = res.all()

        stages = {}
        for stage_val, count, amt in rows:
            stages[stage_val] = {
                "count": count,
                "outstanding_amount": f"{Decimal(str(amt)):.2f}"
            }
        return {
            "stages": stages
        }

    async def get_ar_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        # 1. Dunning Progression (count + outstanding amount by dunning stage)
        dunning_stmt = select(
            ReceivableDbModel.dunning_stage,
            func.count(ReceivableDbModel.id),
            func.coalesce(func.sum(cast(ReceivableDbModel.outstanding_amount, Numeric)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value
        ).group_by(ReceivableDbModel.dunning_stage)
        dunning_rows = (await self._session.execute(dunning_stmt)).all()
        dunning_distribution = {
            row[0]: {
                "count": row[1],
                "outstanding_amount": f"{Decimal(str(row[2])):.2f}"
            } for row in dunning_rows
        }

        # 2. Collection Actions by Status
        actions_stmt = select(
            ReceivableCollectionActionDbModel.status,
            func.count(ReceivableCollectionActionDbModel.id),
            func.coalesce(func.sum(ReceivableCollectionActionDbModel.attempt_count), 0)
        ).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value
        ).group_by(ReceivableCollectionActionDbModel.status)
        actions_rows = (await self._session.execute(actions_stmt)).all()
        
        actions_by_status = {}
        total_retries = 0
        dead_lettered_count = 0
        for status_val, count_val, retries_val in actions_rows:
            actions_by_status[status_val] = count_val
            total_retries += retries_val
            if status_val == "DEAD_LETTERED":
                dead_lettered_count = count_val

        # 3. Average Collection Latency (seconds for COMPLETED actions, limit 100)
        latency_stmt = select(
            ReceivableCollectionActionDbModel.created_at,
            ReceivableCollectionActionDbModel.processed_at
        ).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "COMPLETED",
            ReceivableCollectionActionDbModel.processed_at.isnot(None)
        ).order_by(ReceivableCollectionActionDbModel.processed_at.desc()).limit(100)
        latency_rows = (await self._session.execute(latency_stmt)).all()
        latencies = [(p_at - c_at).total_seconds() for c_at, p_at in latency_rows]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        # 4. Provider / Notification Failure Classification
        failure_stmt = select(
            ReceivableCollectionActionDbModel.failure_classification,
            func.count(ReceivableCollectionActionDbModel.id)
        ).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "FAILED"
        ).group_by(ReceivableCollectionActionDbModel.failure_classification)
        failure_rows = (await self._session.execute(failure_stmt)).all()
        failure_classification_counts = {row[0]: row[1] for row in failure_rows if row[0]}

        # 5. Worker Throughput (Completed actions in last 24h)
        yesterday = Clock.now() - timedelta(days=1)
        throughput_stmt = select(func.count(ReceivableCollectionActionDbModel.id)).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "COMPLETED",
            ReceivableCollectionActionDbModel.processed_at >= yesterday
        )
        throughput_24h = (await self._session.execute(throughput_stmt)).scalar_one() or 0

        # 6. Stuck / Recovered Jobs
        recovered_stmt = select(func.count(ReceivableCollectionActionDbModel.id)).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.attempt_count > 0,
            ReceivableCollectionActionDbModel.status == "RETRYING"
        )
        recovered_jobs = (await self._session.execute(recovered_stmt)).scalar_one() or 0

        # 7. Write-offs & Settlement statistics
        stats_stmt = select(
            func.count(ReceivableDbModel.id),
            func.coalesce(func.sum(cast(ReceivableDbModel.original_amount, Numeric)), 0),
            func.coalesce(func.sum(cast(ReceivableDbModel.allocated_amount, Numeric)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value
        )
        total_count, total_orig, total_allocated = (await self._session.execute(stats_stmt)).fetchone() or (0, 0, 0)

        writeoff_stmt = select(
            func.count(ReceivableDbModel.id),
            func.coalesce(func.sum(cast(ReceivableDbModel.original_amount, Numeric)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.status == "WRITTEN_OFF"
        )
        wo_count, wo_amount = (await self._session.execute(writeoff_stmt)).fetchone() or (0, 0)

        settled_stmt = select(func.count(ReceivableDbModel.id)).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.status == "SETTLED"
        )
        settled_count = (await self._session.execute(settled_stmt)).scalar_one() or 0

        settlement_rate = (settled_count / total_count * 100.0) if total_count > 0 else 100.0

        return {
            "dunning_distribution": dunning_distribution,
            "collection_actions_by_status": actions_by_status,
            "dead_lettered_actions": dead_lettered_count,
            "total_retries": total_retries,
            "average_collection_latency_seconds": avg_latency,
            "failure_classification_counts": failure_classification_counts,
            "worker_throughput_completed_24h": throughput_24h,
            "recovered_jobs": recovered_jobs,
            "total_receivables_count": total_count,
            "total_invoiced_amount": f"{Decimal(str(total_orig)):.2f}",
            "total_allocated_amount": f"{Decimal(str(total_allocated)):.2f}",
            "write_off_count": wo_count,
            "write_off_amount": f"{Decimal(str(wo_amount)):.2f}",
            "settled_count": settled_count,
            "settlement_rate_percentage": settlement_rate
        }

    async def get_ar_health(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        # 1. DB connection test
        try:
            await self._session.execute(select(1))
            db_status = "UP"
        except Exception:
            db_status = "DOWN"

        # 2. Stuck collection actions (> 15 mins in PROCESSING)
        now_dt = Clock.now()
        fifteen_min_ago = now_dt - timedelta(minutes=15)
        stuck_actions_stmt = select(func.count(ReceivableCollectionActionDbModel.id)).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "PROCESSING",
            ReceivableCollectionActionDbModel.created_at < fifteen_min_ago
        )
        stuck_actions = (await self._session.execute(stuck_actions_stmt)).scalar_one() or 0

        # 3. Stuck outbox events (> 15 mins in PROCESSING)
        stuck_events_stmt = select(func.count(OutboxEventDbModel.id)).where(
            OutboxEventDbModel.status == "PROCESSING",
            OutboxEventDbModel.created_at < fifteen_min_ago
        )
        stuck_events = (await self._session.execute(stuck_events_stmt)).scalar_one() or 0

        # 4. Dead lettered actions
        dlq_stmt = select(func.count(ReceivableCollectionActionDbModel.id)).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "DEAD_LETTERED"
        )
        dlq_actions = (await self._session.execute(dlq_stmt)).scalar_one() or 0

        status_val = "HEALTHY"
        if db_status == "DOWN":
            status_val = "UNHEALTHY"
        elif stuck_actions > 0 or stuck_events > 0 or dlq_actions > 0:
            status_val = "DEGRADED"

        return {
            "status": status_val,
            "database": db_status,
            "stuck_collection_actions": stuck_actions,
            "stuck_outbox_events": stuck_events,
            "dead_lettered_collection_actions": dlq_actions,
            "timestamp": now_dt.isoformat()
        }

    async def get_ar_audit_logs(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        ar_event_types = (
            "ReceivableCreated",
            "ReceivablePaymentAllocated",
            "ReceivablePaid",
            "ReceivableWrittenOff",
            "DunningStageChanged",
            "DunningPaused",
            "DunningResumed",
            "CollectionActionScheduled",
            "CollectionActionRetried",
            "CollectionActionReplayed",
            "CollectionActionCancelled",
            "PaymentEventRetried",
            "PaymentEventReplayed",
            "PaymentReceived",
            "PaymentReconciled",
            "PaymentRefunded",
            "PaymentReversed",
            "SettlementCompleted"
        )
        # Dialect-agnostic substring pattern matching for tenant boundaries in payload
        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.event_type.in_(ar_event_types),
            (OutboxEventDbModel.payload.like(f'%"organization_id": "{organization_id.value}"%') | 
             OutboxEventDbModel.payload.like(f'%"organization_id":"{organization_id.value}"%'))
        ).order_by(OutboxEventDbModel.created_at.desc()).limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).scalars().all()
        logs = []
        for ev in rows:
            try:
                payload = json.loads(ev.payload)
            except Exception:
                payload = {}
            metadata = payload.get("_metadata", {})
            
            logs.append({
                "event_id": ev.id,
                "event_type": ev.event_type,
                "timestamp": ev.created_at.isoformat(),
                "processed_at": ev.processed_at.isoformat() if ev.processed_at else None,
                "status": ev.status,
                "correlation_id": metadata.get("correlation_id") or payload.get("correlation_id") or ev.id,
                "causation_id": metadata.get("causation_id") or payload.get("causation_id") or ev.id,
                "actor": metadata.get("actor") or payload.get("user_id") or "SYSTEM",
                "details": {k: v for k, v in payload.items() if k not in ("_metadata", "correlation_id", "causation_id")}
            })
        return logs

    async def get_receivable_timeline(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.id == receivable_id
        ).options(selectinload(ReceivableDbModel.allocations))
        res = await self._session.execute(stmt)
        receivable = res.scalar_one_or_none()
        if not receivable:
            return []

        # Load actions
        actions_stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == receivable_id
        ).order_by(ReceivableCollectionActionDbModel.created_at.asc())
        actions = (await self._session.execute(actions_stmt)).scalars().all()

        # Load outbox events related to invoice_id or receivable_id
        events_stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.payload.like(f'%"invoice_id": "{receivable.invoice_id}"%') |
            OutboxEventDbModel.payload.like(f'%"invoice_id":"{receivable.invoice_id}"%') |
            OutboxEventDbModel.payload.like(f'%"receivable_id": "{receivable_id}"%') |
            OutboxEventDbModel.payload.like(f'%"receivable_id":"{receivable_id}"%')
        ).order_by(OutboxEventDbModel.created_at.asc())
        events = (await self._session.execute(events_stmt)).scalars().all()

        timeline = []

        # Creation entry
        timeline.append({
            "timestamp": receivable.created_at.isoformat(),
            "event_type": "CREATION",
            "description": f"Receivable created for invoice {receivable.invoice_reference} with original amount {receivable.original_amount} {receivable.currency}.",
            "actor": "SYSTEM",
            "correlation_id": receivable.idempotency_key
        })

        # Allocations entries
        for alloc in receivable.allocations:
            timeline.append({
                "timestamp": alloc.allocated_at.isoformat(),
                "event_type": "PAYMENT_ALLOCATION",
                "description": f"Payment allocation of {alloc.allocated_amount} {alloc.currency} applied from payment {alloc.payment_id}.",
                "actor": "SYSTEM",
                "correlation_id": alloc.allocation_reference
            })

        # Collection Actions entries
        for act in actions:
            timeline.append({
                "timestamp": act.created_at.isoformat(),
                "event_type": "COLLECTION_ACTION_SCHEDULED",
                "description": f"Collection action {act.action_type} scheduled for stage {act.action_type.split('_')[-1] if '_' in act.action_type else act.action_type}.",
                "actor": "SYSTEM",
                "correlation_id": act.idempotency_key
            })
            if act.processed_at:
                timeline.append({
                    "timestamp": act.processed_at.isoformat(),
                    "event_type": f"COLLECTION_ACTION_{act.status}",
                    "description": f"Collection action {act.action_type} execution finished with status {act.status}." + (f" Error: {act.error_message}" if act.error_message else ""),
                    "actor": "SYSTEM",
                    "correlation_id": act.idempotency_key
                })

        # Domain Events from Outbox
        seen_events = set()
        for ev in events:
            if ev.id in seen_events:
                continue
            seen_events.add(ev.id)

            try:
                payload = json.loads(ev.payload)
            except Exception:
                payload = {}
            metadata = payload.get("_metadata", {})
            actor = metadata.get("actor") or payload.get("user_id") or "SYSTEM"
            corr_id = metadata.get("correlation_id") or payload.get("correlation_id") or ev.id

            if ev.event_type == "DunningStageChanged":
                timeline.append({
                    "timestamp": ev.created_at.isoformat(),
                    "event_type": "DUNNING_STAGE_CHANGED",
                    "description": f"Dunning stage advanced from {payload.get('previous_stage')} to {payload.get('new_stage')}.",
                    "actor": actor,
                    "correlation_id": corr_id
                })
            elif ev.event_type == "DunningPaused":
                timeline.append({
                    "timestamp": ev.created_at.isoformat(),
                    "event_type": "DUNNING_PAUSED",
                    "description": f"Dunning paused. Reason: {payload.get('reason', 'None specified')}.",
                    "actor": actor,
                    "correlation_id": corr_id
                })
            elif ev.event_type == "DunningResumed":
                timeline.append({
                    "timestamp": ev.created_at.isoformat(),
                    "event_type": "DUNNING_RESUMED",
                    "description": "Dunning resumed.",
                    "actor": actor,
                    "correlation_id": corr_id
                })
            elif ev.event_type == "ReceivableWrittenOff":
                timeline.append({
                    "timestamp": ev.created_at.isoformat(),
                    "event_type": "RECEIVABLE_WRITTEN_OFF",
                    "description": f"Receivable written off. Reason: {payload.get('reason', 'None specified')}.",
                    "actor": actor,
                    "correlation_id": corr_id
                })
            elif ev.event_type == "ReceivablePaid":
                timeline.append({
                    "timestamp": ev.created_at.isoformat(),
                    "event_type": "RECEIVABLE_SETTLED",
                    "description": "Receivable fully settled.",
                    "actor": actor,
                    "correlation_id": corr_id
                })

        timeline.sort(key=lambda x: x["timestamp"])
        return timeline

    async def get_collection_history(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        # Verification check
        stmt_rec = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value,
            ReceivableDbModel.id == receivable_id
        )
        res_rec = await self._session.execute(stmt_rec)
        rec = res_rec.scalar_one_or_none()
        if not rec:
            return []

        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == receivable_id
        ).order_by(ReceivableCollectionActionDbModel.created_at.desc())
        
        actions = (await self._session.execute(stmt)).scalars().all()
        history = []
        for act in actions:
            history.append({
                "id": act.id,
                "action_type": act.action_type,
                "scheduled_at": act.scheduled_at.isoformat(),
                "status": act.status,
                "attempt_count": act.attempt_count,
                "max_attempts": act.max_attempts,
                "next_retry_at": act.next_retry_at.isoformat() if act.next_retry_at else None,
                "processed_at": act.processed_at.isoformat() if act.processed_at else None,
                "error_message": act.error_message,
                "dead_lettered_at": act.dead_lettered_at.isoformat() if act.dead_lettered_at else None,
                "failure_classification": act.failure_classification,
                "idempotency_key": act.idempotency_key
            })
        return history



class PostgresNotificationQueryService(INotificationQueryService):
    """
    SQLAlchemy/PostgreSQL implementation of read-only Notification Reporting Queries.
    Provides dashboard aggregations and delivery attempts auditing records.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_delivery_attempts(
        self,
        organization_id: OrganizationId,
        notification_id: str
    ) -> List[Dict[str, Any]]:
        stmt = select(NotificationDeliveryAttemptDbModel).where(
            NotificationDeliveryAttemptDbModel.organization_id == organization_id.value,
            NotificationDeliveryAttemptDbModel.notification_id == notification_id
        ).order_by(NotificationDeliveryAttemptDbModel.attempt_number.asc())
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "id": m.id,
                "notification_id": m.notification_id,
                "organization_id": m.organization_id,
                "attempt_number": m.attempt_number,
                "channel": m.channel,
                "provider": m.provider,
                "started_at": m.started_at.isoformat(),
                "completed_at": m.completed_at.isoformat(),
                "is_success": m.is_success,
                "provider_reference": m.provider_reference,
                "failure_code": m.failure_code,
                "failure_message": m.failure_message
            } for m in models
        ]

    async def get_notification_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        stmt = select(
            NotificationDbModel.status,
            func.count(NotificationDbModel.id)
        ).where(
            NotificationDbModel.organization_id == organization_id.value
        ).group_by(NotificationDbModel.status)
        res = await self._session.execute(stmt)
        rows = res.all()

        counts = {
            "total": 0,
            "pending": 0,
            "processing": 0,
            "delivered": 0,
            "failed": 0,
            "retrying": 0,
            "cancelled": 0,
            "dead_lettered": 0
        }
        for status_val, count in rows:
            lower_status = status_val.lower()
            if lower_status in counts:
                counts[lower_status] = count
                counts["total"] += count
        return counts

    async def get_delivery_stats(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        stmt = select(
            NotificationDbModel.channel,
            NotificationDbModel.status,
            func.count(NotificationDbModel.id)
        ).where(
            NotificationDbModel.organization_id == organization_id.value
        ).group_by(NotificationDbModel.channel, NotificationDbModel.status)
        res = await self._session.execute(stmt)
        rows = res.all()

        stats = {}
        for channel_val, status_val, count in rows:
            if channel_val not in stats:
                stats[channel_val] = {"delivered": 0, "failed": 0}
            if status_val == "DELIVERED":
                stats[channel_val]["delivered"] += count
            elif status_val == "FAILED":
                stats[channel_val]["failed"] += count

        for ch in ("EMAIL", "WEBHOOK"):
            if ch not in stats:
                stats[ch] = {"delivered": 0, "failed": 0}

        return stats

    async def get_failed_notifications(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        stmt = select(NotificationDbModel).where(
            NotificationDbModel.organization_id == organization_id.value,
            NotificationDbModel.status == "FAILED"
        ).order_by(NotificationDbModel.created_at.desc())
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [self._map_notification_dto(m) for m in models]

    async def get_dead_lettered_notifications(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        stmt = select(NotificationDbModel).where(
            NotificationDbModel.organization_id == organization_id.value,
            NotificationDbModel.status == "DEAD_LETTERED"
        ).order_by(NotificationDbModel.created_at.desc())
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [self._map_notification_dto(m) for m in models]

    async def get_provider_health_states(self) -> List[Dict[str, Any]]:
        stmt = select(ProviderStateDbModel)
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "provider_name": m.provider_name,
                "circuit_state": m.circuit_state,
                "consecutive_failures": m.consecutive_failures,
                "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
                "last_failure_at": m.last_failure_at.isoformat() if m.last_failure_at else None,
                "failure_rate": m.failure_rate
            } for m in models
        ]

    def _map_notification_dto(self, m: NotificationDbModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "organization_id": m.organization_id,
            "reference": m.reference,
            "notification_type": m.notification_type,
            "channel": m.channel,
            "recipient": m.recipient,
            "subject": m.subject,
            "template_id": m.template_id,
            "priority": m.priority,
            "status": m.status,
            "failure_code": m.failure_code,
            "failure_message": m.failure_message,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
            "retry_count": m.retry_count,
            "max_attempts": m.max_attempts,
            "replay_count": m.replay_count
        }


class PostgresWorkflowQueryService(IWorkflowQueryService):
    """
    SQLAlchemy/PostgreSQL implementation of read-only Workflow Queries.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _map_workflow(self, m: WorkflowDbModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "organization_id": m.organization_id,
            "reference": m.reference,
            "name": m.name,
            "description": m.description,
            "status": m.status,
            "active_version_number": m.active_version_number,
            "idempotency_key": m.idempotency_key,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            "archived_at": m.archived_at.isoformat() if m.archived_at else None
        }

    def _map_version(self, m: WorkflowVersionDbModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "workflow_id": m.workflow_id,
            "version": m.version,
            "trigger": json.loads(m.trigger) if m.trigger else None,
            "conditions": json.loads(m.conditions) if m.conditions else None,
            "actions": json.loads(m.actions) if m.actions else [],
            "created_at": m.created_at.isoformat() if m.created_at else None
        }

    async def list_workflows(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        name: Optional[str] = None,
        reference: Optional[str] = None,
        trigger: Optional[str] = None,
        active_version: Optional[int] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        if trigger:
            stmt = select(WorkflowDbModel).join(
                WorkflowVersionDbModel,
                WorkflowVersionDbModel.workflow_id == WorkflowDbModel.id
            ).where(
                WorkflowDbModel.organization_id == organization_id.value
            )
            stmt = stmt.where(WorkflowVersionDbModel.trigger.like(f"%{trigger}%"))
        else:
            stmt = select(WorkflowDbModel).where(WorkflowDbModel.organization_id == organization_id.value)

        if status:
            stmt = stmt.where(WorkflowDbModel.status == status)
        if name:
            stmt = stmt.where(WorkflowDbModel.name.like(f"%{name}%"))
        if reference:
            stmt = stmt.where(WorkflowDbModel.reference == reference)
        if active_version is not None:
            stmt = stmt.where(WorkflowDbModel.active_version_number == active_version)

        stmt = stmt.order_by(WorkflowDbModel.created_at.desc()).limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        
        seen = set()
        unique_models = []
        for m in models:
            if m.id not in seen:
                seen.add(m.id)
                unique_models.append(m)
        return [self._map_workflow(m) for m in unique_models]

    async def get_workflow(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> Optional[Dict[str, Any]]:
        stmt = select(WorkflowDbModel).where(
            WorkflowDbModel.organization_id == organization_id.value,
            WorkflowDbModel.id == workflow_id
        )
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._map_workflow(model) if model else None

    async def list_versions(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> List[Dict[str, Any]]:
        wf = await self.get_workflow(organization_id, workflow_id)
        if not wf:
            return []

        stmt = select(WorkflowVersionDbModel).where(
            WorkflowVersionDbModel.workflow_id == workflow_id
        ).order_by(WorkflowVersionDbModel.version.asc())
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [self._map_version(m) for m in models]

    async def get_version(
        self,
        organization_id: OrganizationId,
        workflow_id: str,
        version: int
    ) -> Optional[Dict[str, Any]]:
        wf = await self.get_workflow(organization_id, workflow_id)
        if not wf:
            return None

        stmt = select(WorkflowVersionDbModel).where(
            WorkflowVersionDbModel.workflow_id == workflow_id,
            WorkflowVersionDbModel.version == version
        )
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._map_version(model) if model else None

    def _map_execution(self, m: WorkflowExecutionDbModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "workflow_id": m.workflow_id,
            "version": m.version,
            "source_event_id": m.source_event_id,
            "source_event_type": m.source_event_type,
            "status": m.status,
            "started_at": m.started_at.isoformat() if m.started_at else None,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            "failure_reason": m.failure_reason,
            "action_results": json.loads(m.action_results) if m.action_results else [],
            "retry_count": m.retry_count,
            "max_attempts": m.max_attempts,
            "next_retry_at": m.next_retry_at.isoformat() if m.next_retry_at else None,
            "failure_classification": m.failure_classification,
            "dead_lettered_at": m.dead_lettered_at.isoformat() if m.dead_lettered_at else None
        }

    async def list_executions(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        workflow_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = select(WorkflowExecutionDbModel).join(
            WorkflowDbModel,
            WorkflowExecutionDbModel.workflow_id == WorkflowDbModel.id
        ).where(
            WorkflowDbModel.organization_id == organization_id.value
        )
        if status:
            stmt = stmt.where(WorkflowExecutionDbModel.status == status)
        if workflow_id:
            stmt = stmt.where(WorkflowExecutionDbModel.workflow_id == workflow_id)
        
        stmt = stmt.order_by(WorkflowExecutionDbModel.started_at.desc()).limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [self._map_execution(m) for m in models]

    async def get_execution(
        self,
        organization_id: OrganizationId,
        execution_id: str
    ) -> Optional[Dict[str, Any]]:
        stmt = select(WorkflowExecutionDbModel).join(
            WorkflowDbModel,
            WorkflowExecutionDbModel.workflow_id == WorkflowDbModel.id
        ).where(
            WorkflowDbModel.organization_id == organization_id.value,
            WorkflowExecutionDbModel.id == execution_id
        )
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._map_execution(model) if model else None

    async def get_workflow_metrics(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> Dict[str, Any]:
        # First verify the workflow belongs to organization
        wf = await self.get_workflow(organization_id, workflow_id)
        if not wf:
            return {}

        # Fetch all executions for this workflow
        stmt = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.workflow_id == workflow_id)
        res = await self._session.execute(stmt)
        executions = res.scalars().all()

        total = len(executions)
        successful = sum(1 for e in executions if e.status == "COMPLETED")
        failed = sum(1 for e in executions if e.status == "FAILED")
        dead_lettered = sum(1 for e in executions if e.status == "DEAD_LETTERED")
        retry_count = sum(e.retry_count for e in executions)

        # Action-level metrics & Durations
        action_success = 0
        action_total = 0
        durations = []
        
        for e in executions:
            action_results = json.loads(e.action_results) if e.action_results else []
            for ar in action_results:
                if ar.get("status") in ("SUCCESS", "COMPLETED"):
                    action_success += 1
                action_total += 1
                if "duration" in ar:
                    durations.append(float(ar["duration"]))
            
            # Overall execution duration
            if e.started_at and e.completed_at:
                durations.append((e.completed_at - e.started_at).total_seconds())

        action_success_rate = (action_success / action_total * 100.0) if action_total > 0 else 100.0
        avg_duration = (sum(durations) / len(durations)) if durations else 0.0

        return {
            "workflow_id": workflow_id,
            "total_executions": total,
            "successful_executions": successful,
            "failed_executions": failed,
            "dead_letter_count": dead_lettered,
            "retry_count": retry_count,
            "action_success_rate_percentage": action_success_rate,
            "average_duration_seconds": avg_duration
        }
