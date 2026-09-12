from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from typing import Optional
from sqlalchemy import select

from ...domain.organization.value_objects import OrganizationId
from ...domain.settlement_reconciliation.settlement import Settlement, SettlementStatus
from ...domain.settlement_reconciliation.value_objects import SettlementId, SettlementAmount
from ...domain.settlement_reconciliation.reconciliation import ReconciliationStatus
from ...domain.settlement_reconciliation.adjustments import AdjustmentType, SettlementAdjustment
from ...domain.settlement_reconciliation.reconciliation_engine import ReconciliationEngine
from ...domain.settlement_reconciliation.specifications import IsSettlementBalanced
from ...domain.shared.result.result import Result, ResultError
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import SettlementReconciliationDbModel
from ...infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
from ...presentation.api.dependencies.context import ExecutionContext
from ..settlement_reconciliation.errors import SettlementReconciliationErrors


class ReconciliationAutomationService:
    """
    Hardened application service that coordinates automated reconciliation by locking settlements,
    handling transient/permanent errors, applying retries with backoff, and recovering stuck processes.
    """
    def __init__(self, settlement_repo, source_tx_repo, engine: Optional[ReconciliationEngine] = None):
        self._settlement_repo = settlement_repo
        self._source_tx_repo = source_tx_repo
        self._engine = engine or ReconciliationEngine()

    async def reconcile_settlement(
        self,
        settlement_id: str,
        organization_id: str,
        ctx: ExecutionContext
    ) -> Result[Settlement]:
        # Enforce tenant isolation boundary
        if organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        # 1. Acquire DB lock on settlement aggregate
        settlement = await self._settlement_repo.get_by_id(SettlementId(settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())
        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        # Check operational pause
        if settlement.paused:
            return Result.ok(settlement)

        # If already completed, cancelled, failed or dead-lettered, skip
        if settlement.status in (SettlementStatus.COMPLETED, SettlementStatus.CANCELLED):
            return Result.ok(settlement)
        if settlement.processing_state in ("DEAD_LETTERED", "FAILED", "CANCELLED"):
            return Result.ok(settlement)

        # Transition processing state to PROCESSING
        settlement.start_processing()
        await self._settlement_repo.save(settlement)

        try:
            # 2. Transition draft/open to RECONCILING dunning state
            if settlement.status == SettlementStatus.DRAFT:
                settlement.open()
            
            if settlement.status in (SettlementStatus.OPEN, SettlementStatus.DISCREPANCY):
                settlement.start_reconciliation()

            # 3. Load relevant source transactions
            source_txs = await self._source_tx_repo.find_by_period(
                settlement.organization_id,
                settlement.period.start_date,
                settlement.period.end_date
            )

            # 4. Execute matching policies
            rec_result = self._engine.reconcile(settlement, source_txs)

            # 5. Apply auto-corrections for resolved amount discrepancies
            for disc in rec_result.discrepancies:
                if disc.resolved and disc.discrepancy_type == "AMOUNT_MISMATCH":
                    variance = disc.difference.amount.amount
                    if variance != 0:
                        adjustment_id = f"auto-corr-{disc.entry_reference.value}-{uuid4().hex[:6]}"
                        adjustment = SettlementAdjustment(
                            id=adjustment_id,
                            type=AdjustmentType.MANUAL_ADJUSTMENT,
                            description=f"Auto-resolution of minor variance on entry {disc.entry_reference.value}",
                            amount=SettlementAmount(-variance),
                            currency=settlement.currency
                        )
                        settlement.apply_adjustment(adjustment)

            # 6. Apply reconciliation result to aggregate
            reconcile_res = settlement.reconcile(rec_result)
            if reconcile_res.is_failure:
                raise ValueError(reconcile_res.error.message)

            # 7. Auto-complete balanced settlements
            if settlement.status == SettlementStatus.RECONCILED:
                if IsSettlementBalanced().is_satisfied_by(settlement):
                    complete_res = settlement.complete()
                    if complete_res.is_failure:
                        raise ValueError(complete_res.error.message)

            # Mark processing as completed successfully
            settlement.complete_processing()
            await self._settlement_repo.save(settlement)

        except Exception as e:
            # Classify error: DB connection/network/timeout errors are transient
            err_str = str(e).lower()
            is_transient = any(kw in err_str for kw in ("timeout", "connection", "locked", "deadlock", "network", "operationalerror", "temporary"))
            
            settlement.fail_processing(str(e), is_transient=is_transient)
            try:
                await self._settlement_repo.save(settlement)
            except Exception:
                pass # prevent secondary saving failures from masking primary error
            
            return Result.fail(ResultError.unexpected(str(e)))

        return Result.ok(settlement)

    async def recover_stuck_settlements(self, timeout_minutes: int = 15) -> int:
        """Finds and recovers settlement batches stuck in PROCESSING status due to worker crash."""
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        async with session_manager.session() as session:
            stmt = (
                select(SettlementReconciliationDbModel.id, SettlementReconciliationDbModel.organization_id)
                .where(
                    SettlementReconciliationDbModel.processing_state == "PROCESSING",
                    SettlementReconciliationDbModel.processing_started_at < cutoff
                )
            )
            
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)
                
            res = await session.execute(stmt)
            rows = res.all()
            
        recovered_count = 0
        for row in rows:
            settlement_id, org_id = row[0], row[1]
            async with session_manager.session() as work_session:
                repo = PostgresSettlementRepository(work_session)
                settlement = await repo.get_by_id(SettlementId(settlement_id), lock=True)
                if settlement and settlement.processing_state == "PROCESSING":
                    settlement.recover()
                    await repo.save(settlement)
                    await work_session.commit()
                    recovered_count += 1
                    
        return recovered_count
