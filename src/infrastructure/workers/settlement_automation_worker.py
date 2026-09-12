from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import traceback

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import SettlementReconciliationDbModel
from ...application.settlement_reconciliation.automation_service import ReconciliationAutomationService
from ...infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
from ...infrastructure.database.source_transaction_repository import PostgresSourceTransactionRepository
from ...presentation.api.dependencies.context import ExecutionContext


class SettlementAutomationWorker:
    """
    Background worker that runs automatically to discover eligible settlements,
    acquires locks, and invokes the reconciliation engine to reconcile them.
    """
    def __init__(self, automation_service: Optional[ReconciliationAutomationService] = None):
        self._automation_service = automation_service

    async def process_eligible_settlements(self) -> int:
        processed_count = 0
        
        from datetime import datetime
        now = datetime.utcnow()
        async with session_manager.session() as session:
            # Query for settlements that are not completed/cancelled, not paused, and due for retry
            stmt = (
                select(SettlementReconciliationDbModel.id, SettlementReconciliationDbModel.organization_id)
                .where(
                    SettlementReconciliationDbModel.paused == False,
                    SettlementReconciliationDbModel.processing_state.in_(["PENDING", "RETRYING"]),
                    (SettlementReconciliationDbModel.next_retry_at == None) |
                    (SettlementReconciliationDbModel.next_retry_at <= now)
                )
            )
            
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)

            res = await session.execute(stmt)
            rows = res.all()

        for row in rows:
            settlement_id, org_id = row[0], row[1]
            async with session_manager.session() as work_session:
                settlement_repo = PostgresSettlementRepository(work_session)
                source_tx_repo = PostgresSourceTransactionRepository(work_session)
                service = self._automation_service or ReconciliationAutomationService(settlement_repo, source_tx_repo)

                ctx = ExecutionContext(
                    user_id="settlement_automation_worker",
                    organization_id=org_id,
                    role="SYSTEM"
                )

                try:
                    res = await service.reconcile_settlement(settlement_id, org_id, ctx)
                    if res.is_success:
                        await work_session.commit()
                        processed_count += 1
                    else:
                        print(f"[Worker] Failed to reconcile settlement {settlement_id}: {res.error.message}")
                        await work_session.rollback()
                except Exception as e:
                    print(f"[Worker] Exception reconciling settlement {settlement_id}: {str(e)}")
                    print(traceback.format_exc())
                    await work_session.rollback()

        return processed_count
