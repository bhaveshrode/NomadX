from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.models import (
    InvoiceDbModel,
    ReceivableDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    SettlementDbModel
)


class NotificationEventMapper:
    """
    Orchestrates the construction of template variable dictionaries from
    transactional outbox event payloads and related database entities.
    """
    async def construct_variables(
        self,
        session: AsyncSession,
        event_type: str,
        payload: dict
    ) -> dict:
        variables = {}

        if event_type == "InvoiceIssued":
            invoice_id = payload.get("invoice_id")
            if invoice_id:
                stmt = select(InvoiceDbModel).where(InvoiceDbModel.id == invoice_id)
                res = await session.execute(stmt)
                inv = res.scalar_one_or_none()
                if inv:
                    variables = {
                        "customer_name": inv.billing_name or "Customer",
                        "amount": f"{Decimal(inv.grand_total):.2f}",
                        "currency": inv.currency_code,
                        "invoice_number": inv.invoice_number or "N/A"
                    }

        elif event_type == "PaymentConfirmed":
            payment_id = payload.get("payment_id")
            if payment_id:
                stmt = select(PaymentDbModel).where(PaymentDbModel.id == payment_id)
                res = await session.execute(stmt)
                pmt = res.scalar_one_or_none()
                if pmt:
                    # Find first invoice allocation to get billing customer name
                    inv_stmt = select(InvoiceDbModel.billing_name).join(
                        PaymentAllocationDbModel, PaymentAllocationDbModel.invoice_id == InvoiceDbModel.id
                    ).where(PaymentAllocationDbModel.payment_id == payment_id)
                    inv_res = await session.execute(inv_stmt)
                    cust_name = inv_res.scalar_one_or_none() or "Customer"

                    variables = {
                        "customer_name": cust_name,
                        "amount": f"{Decimal(pmt.observed_amount):.2f}",
                        "currency": pmt.asset_code,
                        "payment_reference": pmt.transaction_reference or pmt.id
                    }

        elif event_type == "PaymentReconciled":
            payment_id = payload.get("payment_id")
            invoice_id = payload.get("invoice_id")
            allocated_amount = payload.get("allocated_amount", "0.00")

            if invoice_id:
                stmt = select(InvoiceDbModel).where(InvoiceDbModel.id == invoice_id)
                res = await session.execute(stmt)
                inv = res.scalar_one_or_none()
                if inv:
                    pmt_ref = "N/A"
                    if payment_id:
                        pmt_stmt = select(PaymentDbModel.transaction_reference).where(PaymentDbModel.id == payment_id)
                        pmt_res = await session.execute(pmt_stmt)
                        pmt_ref = pmt_res.scalar_one_or_none() or payment_id

                    variables = {
                        "customer_name": inv.billing_name or "Customer",
                        "amount": f"{Decimal(allocated_amount):.2f}",
                        "currency": inv.currency_code,
                        "invoice_number": inv.invoice_number or "N/A",
                        "payment_reference": pmt_ref
                    }

        elif event_type in ("ReceivableBecameOverdue", "ReceivableCollectionAttentionRequired", "DunningStageChanged"):
            receivable_id = payload.get("receivable_id")
            if receivable_id:
                stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == receivable_id)
                res = await session.execute(stmt)
                rec = res.scalar_one_or_none()
                if rec:
                    inv_stmt = select(InvoiceDbModel).where(InvoiceDbModel.id == rec.invoice_id)
                    inv_res = await session.execute(inv_stmt)
                    inv = inv_res.scalar_one_or_none()
                    if inv:
                        from ...domain.shared.clock.clock import Clock
                        days = (Clock.now() - rec.due_date).days if rec.due_date else 0
                        variables = {
                            "customer_name": inv.billing_name or "Customer",
                            "invoice_reference": rec.invoice_reference or "N/A",
                            "receivable_reference": rec.reference or "N/A",
                            "outstanding_amount": f"{Decimal(rec.outstanding_amount):.2f}",
                            "currency": rec.currency,
                            "due_date": rec.due_date.strftime("%Y-%m-%d") if rec.due_date else "N/A",
                            "days_overdue": str(max(0, days)),
                            "dunning_stage": rec.dunning_stage or "NONE",
                            "payment_information": "Please follow payment instructions on the invoice."
                        }

        elif event_type in ("SettlementSubmitted", "SettlementCompleted", "SettlementFailed"):
            settlement_id = payload.get("settlement_id")
            if settlement_id:
                stmt = select(SettlementDbModel).where(SettlementDbModel.id == settlement_id)
                res = await session.execute(stmt)
                setl = res.scalar_one_or_none()
                if setl:
                    variables = {
                        "settlement_id": setl.id,
                        "reference": setl.reference,
                        "amount": f"{Decimal(setl.amount):.2f}",
                        "currency": setl.asset_code,
                        "destination": setl.destination_identifier or "N/A",
                        "status": setl.status,
                        "failure_code": setl.failure_code or payload.get("failure_code") or "N/A",
                        "failure_message": setl.failure_message or payload.get("failure_message") or "N/A"
                    }

        return variables
