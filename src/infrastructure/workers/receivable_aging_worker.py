from datetime import datetime
from decimal import Decimal
from sqlalchemy import select, cast, Numeric

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import ReceivableDbModel
from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.repositories import PostgresReceivableRepository
from ...application.receivable.aging import EvaluateReceivableAgingCommand, EvaluateReceivableAgingHandler
from ...presentation.api.dependencies.context import ExecutionContext


class ReceivableAgingWorker:
    """
    Worker coordinating time-driven aging and collection transitions of Receivables.
    Executes periodically to process outstanding receivables whose due dates have passed.
    """
    async def process_aging(self) -> int:
        """
        Polls for organizations with overdue candidate receivables,
        evaluates their aging status inside separate transactions, and commits.
        """
        current_time = Clock.now()

        # Query distinct organizations with outstanding candidate receivables
        async with session_manager.session() as session:
            stmt = select(ReceivableDbModel.organization_id).where(
                cast(ReceivableDbModel.outstanding_amount, Numeric) > 0,
                ReceivableDbModel.due_date <= current_time
            ).distinct()
            res = await session.execute(stmt)
            org_ids = [row[0] for row in res.all()]

        if not org_ids:
            return 0

        total_evaluated = 0

        for org_id in org_ids:
            async with session_manager.session() as session:
                try:
                    receivable_repo = PostgresReceivableRepository(session)
                    handler = EvaluateReceivableAgingHandler(receivable_repo)

                    ctx = ExecutionContext(
                        user_id="receivable_aging_worker",
                        organization_id=org_id,
                        role="SYSTEM"
                    )
                    cmd = EvaluateReceivableAgingCommand(organization_id=org_id)

                    res = await handler.handle(cmd, ctx)
                    if res.is_success:
                        total_evaluated += res.value
                        await session.commit()
                    else:
                        raise ValueError(f"Failed to evaluate aging for org {org_id}: {res.error.message}")
                except Exception as e:
                    # Aborts the current transaction and propagates
                    raise e

        return total_evaluated
