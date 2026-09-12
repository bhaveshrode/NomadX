from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List
import json
from uuid import uuid4
from sqlalchemy import select, func, cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...application.financial_consistency.query_service import IFinancialConsistencyQueryService
from .models import (
    InvoiceDbModel,
    ReceivableDbModel,
    ReceivableAllocationDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    SettlementEntryDbModel,
    SettlementAdjustmentDbModel,
    SettlementReconciliationDbModel,
    SettlementDbModel,
    OutboxEventDbModel,
    FinancialConsistencyViolationDbModel
)


class PostgresFinancialConsistencyQueryService(IFinancialConsistencyQueryService):
    """
    SQLAlchemy-backed implementation of IFinancialConsistencyQueryService.
    Continuously monitors and validates cross-context financial invariants,
    persisting findings for full auditability and traceability.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check_financial_consistency(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        detected_violations = []

        # 1. Invoice vs Receivable Consistency Check
        stmt_inv = (
            select(InvoiceDbModel, ReceivableDbModel)
            .outerjoin(ReceivableDbModel, InvoiceDbModel.id == ReceivableDbModel.invoice_id)
            .where(
                InvoiceDbModel.organization_id == org_id,
                InvoiceDbModel.status == "ISSUED"
            )
        )
        res_inv = await self._session.execute(stmt_inv)
        rows_inv = res_inv.all()
        for inv, rec in rows_inv:
            if not rec:
                detected_violations.append({
                    "violation_type": "INVOICE_RECEIVABLE_MISMATCH",
                    "context": "Invoice",
                    "entity_id": inv.id,
                    "severity": "CRITICAL",
                    "classification": "INVOICE_INTEGRITY",
                    "expected_value": inv.grand_total,
                    "actual_value": "None",
                    "description": f"Receivable record is missing for issued invoice {inv.invoice_number}."
                })
            else:
                inv_total = Decimal(inv.grand_total)
                rec_orig = Decimal(rec.original_amount)
                if inv_total != rec_orig:
                    detected_violations.append({
                        "violation_type": "INVOICE_RECEIVABLE_MISMATCH",
                        "context": "Invoice",
                        "entity_id": inv.id,
                        "severity": "CRITICAL",
                        "classification": "INVOICE_INTEGRITY",
                        "expected_value": str(inv_total),
                        "actual_value": str(rec_orig),
                        "description": f"Receivable original amount {rec_orig} does not match invoice grand total {inv_total}."
                    })

        # 2. Payment vs Receivable Allocation Consistency Check
        stmt_pay = (
            select(PaymentDbModel)
            .where(PaymentDbModel.organization_id == org_id)
        )
        res_pay = await self._session.execute(stmt_pay)
        payments = res_pay.scalars().all()
        for pay in payments:
            # Query payment allocations
            stmt_pa = (
                select(func.sum(cast(PaymentAllocationDbModel.allocated_amount, Numeric)))
                .where(PaymentAllocationDbModel.payment_id == pay.id)
            )
            res_pa = await self._session.execute(stmt_pa)
            pa_sum = res_pa.scalar() or Decimal("0.00")

            # Query receivable allocations
            stmt_ra = (
                select(func.sum(cast(ReceivableAllocationDbModel.allocated_amount, Numeric)))
                .where(ReceivableAllocationDbModel.payment_reference == pay.id)
            )
            res_ra = await self._session.execute(stmt_ra)
            ra_sum = res_ra.scalar() or Decimal("0.00")

            if pa_sum != ra_sum:
                detected_violations.append({
                    "violation_type": "PAYMENT_ALLOCATION_MISMATCH",
                    "context": "Payment",
                    "entity_id": pay.id,
                    "severity": "CRITICAL",
                    "classification": "PAYMENT_INTEGRITY",
                    "expected_value": str(pa_sum),
                    "actual_value": str(ra_sum),
                    "description": f"Payment allocations total {pa_sum} does not match receivable allocations total {ra_sum}."
                })

            if pay.reconciliation_status == "RECONCILED":
                pay_amt = Decimal(pay.observed_amount)
                if pa_sum != pay_amt:
                    detected_violations.append({
                        "violation_type": "PAYMENT_ALLOCATION_MISMATCH",
                        "context": "Payment",
                        "entity_id": pay.id,
                        "severity": "CRITICAL",
                        "classification": "PAYMENT_INTEGRITY",
                        "expected_value": str(pay_amt),
                        "actual_value": str(pa_sum),
                        "description": f"Reconciled payment observed amount {pay_amt} does not match payment allocations total {pa_sum}."
                    })

        # 3. Receivable Balance Invariant Check
        stmt_rec = (
            select(ReceivableDbModel)
            .where(ReceivableDbModel.organization_id == org_id)
        )
        res_rec = await self._session.execute(stmt_rec)
        receivables = res_rec.scalars().all()
        for rec in receivables:
            orig = Decimal(rec.original_amount)
            alloc = Decimal(rec.allocated_amount)
            outstanding = Decimal(rec.outstanding_amount)
            
            if orig - alloc != outstanding:
                detected_violations.append({
                    "violation_type": "RECEIVABLE_BALANCE_MISMATCH",
                    "context": "Receivable",
                    "entity_id": rec.id,
                    "severity": "CRITICAL",
                    "classification": "RECEIVABLE_INTEGRITY",
                    "expected_value": str(orig - alloc),
                    "actual_value": str(outstanding),
                    "description": f"Receivable outstanding balance {outstanding} does not equal original amount minus allocated amount ({orig - alloc})."
                })

            stmt_ra = (
                select(func.sum(cast(ReceivableAllocationDbModel.allocated_amount, Numeric)))
                .where(ReceivableAllocationDbModel.receivable_id == rec.id)
            )
            res_ra = await self._session.execute(stmt_ra)
            ra_sum = res_ra.scalar() or Decimal("0.00")
            if alloc != ra_sum:
                detected_violations.append({
                    "violation_type": "RECEIVABLE_BALANCE_MISMATCH",
                    "context": "Receivable",
                    "entity_id": rec.id,
                    "severity": "CRITICAL",
                    "classification": "RECEIVABLE_INTEGRITY",
                    "expected_value": str(alloc),
                    "actual_value": str(ra_sum),
                    "description": f"Receivable allocated amount column {alloc} does not match sum of allocations in database {ra_sum}."
                })

        # 4. Payment Transaction vs Settlement Entry Consistency
        stmt_se = (
            select(SettlementEntryDbModel, PaymentDbModel)
            .join(SettlementReconciliationDbModel, SettlementEntryDbModel.settlement_id == SettlementReconciliationDbModel.id)
            .join(PaymentDbModel, SettlementEntryDbModel.source_transaction_reference == PaymentDbModel.transaction_reference)
            .where(
                SettlementReconciliationDbModel.organization_id == org_id,
                SettlementEntryDbModel.source_context_type == "PAYMENT"
            )
        )
        res_se = await self._session.execute(stmt_se)
        rows_se = res_se.all()
        for entry, payment in rows_se:
            entry_net = Decimal(entry.net_amount)
            pay_obs = Decimal(payment.observed_amount)
            if entry_net != pay_obs:
                detected_violations.append({
                    "violation_type": "SETTLEMENT_ENTRY_MISMATCH",
                    "context": "Settlement",
                    "entity_id": entry.id,
                    "severity": "CRITICAL",
                    "classification": "SETTLEMENT_INTEGRITY",
                    "expected_value": str(pay_obs),
                    "actual_value": str(entry_net),
                    "description": f"Settlement entry net amount {entry_net} does not match payment observed amount {pay_obs} for transaction reference {entry.source_transaction_reference}."
                })

        # 5. Settlement Entry vs Settlement Totals Consistency Check
        stmt_sr = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.organization_id == org_id)
        )
        res_sr = await self._session.execute(stmt_sr)
        batches = res_sr.scalars().all()
        for batch in batches:
            stmt_e = (
                select(SettlementEntryDbModel)
                .where(SettlementEntryDbModel.settlement_id == batch.id)
            )
            res_e = await self._session.execute(stmt_e)
            entries = res_e.scalars().all()
            
            stmt_a = (
                select(SettlementAdjustmentDbModel)
                .where(SettlementAdjustmentDbModel.settlement_id == batch.id)
            )
            res_a = await self._session.execute(stmt_a)
            adjs = res_a.scalars().all()

            gross_total = sum(Decimal(entry.gross_amount) for entry in entries)
            fees_total = sum(Decimal(entry.fees_amount) for entry in entries)
            entry_adjs = sum(Decimal(entry.adjustments_amount) for entry in entries)
            agg_adjs = sum(Decimal(adj.amount) for adj in adjs)
            adjustments_total = entry_adjs + agg_adjs
            calculated_net = gross_total - fees_total - adjustments_total

            stmt_phys = (
                select(SettlementDbModel)
                .where(
                    SettlementDbModel.organization_id == org_id,
                    SettlementDbModel.reference == batch.reference
                )
            )
            res_phys = await self._session.execute(stmt_phys)
            phys = res_phys.scalar_one_or_none()
            if phys:
                phys_amt = Decimal(phys.amount)
                if phys_amt != calculated_net:
                    detected_violations.append({
                        "violation_type": "SETTLEMENT_TOTALS_MISMATCH",
                        "context": "Settlement",
                        "entity_id": batch.id,
                        "severity": "CRITICAL",
                        "classification": "SETTLEMENT_INTEGRITY",
                        "expected_value": str(phys_amt),
                        "actual_value": str(calculated_net),
                        "description": f"Physical settlement amount {phys_amt} does not match reconciliation batch calculated net amount {calculated_net}."
                    })

            # 6. Net Settlement Invariant Check: gross - fees - adjustments == net_amount
            for entry in entries:
                eg = Decimal(entry.gross_amount)
                ef = Decimal(entry.fees_amount)
                ea = Decimal(entry.adjustments_amount)
                en = Decimal(entry.net_amount)
                if eg - ef - ea != en:
                    detected_violations.append({
                        "violation_type": "NET_SETTLEMENT_INVARIANT_VIOLATION",
                        "context": "Settlement",
                        "entity_id": entry.id,
                        "severity": "CRITICAL",
                        "classification": "SETTLEMENT_INTEGRITY",
                        "expected_value": str(eg - ef - ea),
                        "actual_value": str(en),
                        "description": f"Settlement entry {entry.id} violates net amount invariant: net amount {en} != gross ({eg}) - fees ({ef}) - adjustments ({ea})."
                    })

        # --- Persistence Management ---
        # Query all existing violations in DB for this organization
        stmt_exist = select(FinancialConsistencyViolationDbModel).where(FinancialConsistencyViolationDbModel.organization_id == org_id)
        res_exist = await self._session.execute(stmt_exist)
        existing_violations = res_exist.scalars().all()
        existing_map = {(v.violation_type, v.entity_id): v for v in existing_violations}

        # Track keys processed in this run
        processed_keys = set()

        for dv in detected_violations:
            key = (dv["violation_type"], dv["entity_id"])
            processed_keys.add(key)
            if key in existing_map:
                ev = existing_map[key]
                # Re-activate / update violation
                ev.resolved = False
                ev.resolved_at = None
                ev.expected_value = dv["expected_value"]
                ev.actual_value = dv["actual_value"]
                ev.description = dv["description"]
                ev.severity = dv["severity"]
                ev.classification = dv["classification"]
            else:
                # Add new violation record
                new_v = FinancialConsistencyViolationDbModel(
                    id=str(uuid4()),
                    organization_id=org_id,
                    violation_type=dv["violation_type"],
                    context=dv["context"],
                    entity_id=dv["entity_id"],
                    severity=dv["severity"],
                    classification=dv["classification"],
                    expected_value=dv["expected_value"],
                    actual_value=dv["actual_value"],
                    description=dv["description"],
                    resolved=False,
                    detected_at=datetime.utcnow(),
                    resolved_at=None
                )
                self._session.add(new_v)

        # Mark no longer detected violations as resolved
        for key, ev in existing_map.items():
            if key not in processed_keys and not ev.resolved:
                ev.resolved = True
                ev.resolved_at = datetime.utcnow()

        await self._session.flush()

        report_status = "HEALTHY" if not detected_violations else "DEGRADED"

        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": report_status,
            "summary": {
                "total_invoices_checked": len(rows_inv),
                "total_receivables_checked": len(receivables),
                "total_payments_checked": len(payments),
                "total_settlements_checked": len(batches),
                "total_violations": len(detected_violations)
            },
            "violations": detected_violations
        }

    async def get_consistency_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        report = await self.check_financial_consistency(organization_id)
        org_id = organization_id.value
        
        # Query total active violations
        stmt_act = (
            select(func.count(FinancialConsistencyViolationDbModel.id))
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.resolved == False
            )
        )
        res_act = await self._session.execute(stmt_act)
        active_count = res_act.scalar() or 0

        # Query total resolved violations
        stmt_res = (
            select(func.count(FinancialConsistencyViolationDbModel.id))
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.resolved == True
            )
        )
        res_res = await self._session.execute(stmt_res)
        resolved_count = res_res.scalar() or 0

        total_checks = (
            report["summary"]["total_invoices_checked"] +
            report["summary"]["total_receivables_checked"] +
            report["summary"]["total_payments_checked"] +
            report["summary"]["total_settlements_checked"]
        )
        
        health_score = 100.0
        if total_checks > 0:
            health_score = max(0.0, 100.0 - (active_count / total_checks * 100.0))

        return {
            "organization_id": org_id,
            "total_checks_run": total_checks,
            "active_violations": active_count,
            "resolved_violations": resolved_count,
            "health_score": f"{health_score:.1f}",
            "status": report["status"],
            "last_check_run_at": report["checked_at"]
        }

    async def get_active_violations(
        self,
        organization_id: OrganizationId
    ) -> list[Dict[str, Any]]:
        stmt = (
            select(FinancialConsistencyViolationDbModel)
            .where(
                FinancialConsistencyViolationDbModel.organization_id == organization_id.value,
                FinancialConsistencyViolationDbModel.resolved == False
            )
        )
        res = await self._session.execute(stmt)
        violations = res.scalars().all()
        return [
            {
                "id": v.id,
                "organization_id": v.organization_id,
                "violation_type": v.violation_type,
                "context": v.context,
                "entity_id": v.entity_id,
                "severity": v.severity,
                "classification": v.classification,
                "expected_value": v.expected_value,
                "actual_value": v.actual_value,
                "description": v.description,
                "resolved": v.resolved,
                "detected_at": v.detected_at.isoformat() if v.detected_at else None,
                "resolved_at": v.resolved_at.isoformat() if v.resolved_at else None
            }
            for v in violations
        ]

    async def get_violation_history(
        self,
        organization_id: OrganizationId
    ) -> list[Dict[str, Any]]:
        stmt = (
            select(FinancialConsistencyViolationDbModel)
            .where(FinancialConsistencyViolationDbModel.organization_id == organization_id.value)
            .order_by(FinancialConsistencyViolationDbModel.detected_at.desc())
        )
        res = await self._session.execute(stmt)
        violations = res.scalars().all()
        return [
            {
                "id": v.id,
                "organization_id": v.organization_id,
                "violation_type": v.violation_type,
                "context": v.context,
                "entity_id": v.entity_id,
                "severity": v.severity,
                "classification": v.classification,
                "expected_value": v.expected_value,
                "actual_value": v.actual_value,
                "description": v.description,
                "resolved": v.resolved,
                "detected_at": v.detected_at.isoformat() if v.detected_at else None,
                "resolved_at": v.resolved_at.isoformat() if v.resolved_at else None
            }
            for v in violations
        ]

    async def get_entity_timeline(
        self,
        organization_id: OrganizationId,
        entity_id: str
    ) -> list[Dict[str, Any]]:
        org_id = organization_id.value
        timeline = []

        # 1. Fetch outbox events matching organization and payload reference
        stmt_ev = select(OutboxEventDbModel).order_by(OutboxEventDbModel.created_at.asc())
        res_ev = await self._session.execute(stmt_ev)
        events = res_ev.scalars().all()
        
        for ev in events:
            try:
                payload = json.loads(ev.payload)
                # Verify tenant boundary
                ev_org_id = payload.get("organization_id") or payload.get("_metadata", {}).get("organization_id")
                if ev_org_id and ev_org_id != org_id:
                    continue
                
                # Check correlation/causation/entity mapping
                metadata = payload.get("_metadata", {})
                corr_id = metadata.get("correlation_id") or payload.get("correlation_id")
                caus_id = metadata.get("causation_id") or payload.get("causation_id")
                
                matches_entity = (
                    any(str(val) == entity_id for val in payload.values()) or
                    ev.id == entity_id or
                    corr_id == entity_id or
                    caus_id == entity_id
                )
                
                if matches_entity:
                    timeline.append({
                        "timestamp": ev.created_at.isoformat() if ev.created_at else datetime.utcnow().isoformat(),
                        "type": "DOMAIN_EVENT",
                        "activity": ev.event_type,
                        "description": f"Domain event '{ev.event_type}' processed for entity.",
                        "metadata": {
                            "event_id": ev.id,
                            "correlation_id": corr_id,
                            "causation_id": caus_id,
                            "payload": payload
                        }
                    })
            except Exception:
                pass

        # 2. Fetch consistency findings for this entity in the organization
        stmt_viol = (
            select(FinancialConsistencyViolationDbModel)
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.entity_id == entity_id
            )
        )
        res_viol = await self._session.execute(stmt_viol)
        violations = res_viol.scalars().all()
        for v in violations:
            timeline.append({
                "timestamp": v.detected_at.isoformat() if v.detected_at else datetime.utcnow().isoformat(),
                "type": "CONSISTENCY_VIOLATION_DETECTED",
                "activity": v.violation_type,
                "description": f"Consistency violation '{v.violation_type}' detected: {v.description}",
                "metadata": {
                    "violation_id": v.id,
                    "severity": v.severity,
                    "classification": v.classification,
                    "expected_value": v.expected_value,
                    "actual_value": v.actual_value
                }
            })
            if v.resolved and v.resolved_at:
                timeline.append({
                    "timestamp": v.resolved_at.isoformat(),
                    "type": "CONSISTENCY_VIOLATION_RESOLVED",
                    "activity": v.violation_type,
                    "description": f"Consistency violation '{v.violation_type}' resolved: discrepancy corrected.",
                    "metadata": {
                        "violation_id": v.id
                    }
                })

        # Sort timeline chronologically by timestamp
        timeline.sort(key=lambda x: x["timestamp"])
        return timeline
