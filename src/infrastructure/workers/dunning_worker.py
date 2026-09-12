from datetime import datetime
from uuid import uuid4
from sqlalchemy import select, cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import ReceivableDbModel
from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.repositories import PostgresReceivableRepository, PostgresCollectionActionRepository
from ...domain.receivable.value_objects import ReceivableId
from ...domain.receivable.collection_action import CollectionAction, CollectionActionId
from ...application.receivable.commands import AdvanceDunningStageCommand
from ...application.receivable.handlers import AdvanceDunningStageHandler
from ...application.receivable.dunning_policy import DunningPolicy
from ...presentation.api.dependencies.context import ExecutionContext


class DunningWorker:
    """
    Worker coordinating background dunning/collection stage advancement of overdue receivables.
    Runs periodically to evaluate and advance collection status and schedule communication actions.
    """
    def __init__(self, dunning_policy: DunningPolicy):
        self.dunning_policy = dunning_policy

    async def process_dunning(self) -> int:
        """
        Identifies overdue receivables that have reached their next dunning trigger time,
        advances their stage, and schedules the corresponding collection action safely.
        """
        current_time = Clock.now()

        async with session_manager.session() as session:
            # Query for overdue receivables requiring dunning assessment
            stmt = (
                select(ReceivableDbModel)
                .where(
                    ReceivableDbModel.status == "OVERDUE",
                    ReceivableDbModel.dunning_paused == False,
                    ReceivableDbModel.next_dunning_at <= current_time,
                    cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
                )
            )
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)

            res = await session.execute(stmt)
            receivable_models = res.scalars().all()
            if not receivable_models:
                return 0

            receivable_repo = PostgresReceivableRepository(session)
            collection_action_repo = PostgresCollectionActionRepository(session)
            handler = AdvanceDunningStageHandler(receivable_repo)

            processed_count = 0

            for rec_model in receivable_models:
                try:
                    # Load aggregate with pessimistic locking
                    receivable = await receivable_repo.get_by_id(ReceivableId(rec_model.id), lock=True)
                    if not receivable:
                        continue

                    if receivable.status != "OVERDUE" or receivable.outstanding_amount.amount <= 0 or receivable.dunning_paused:
                        continue

                    current_stage = receivable.dunning_state.stage
                    next_stage = self.dunning_policy.get_next_stage(current_stage)
                    if not next_stage:
                        continue

                    rule = self.dunning_policy.get_rule(next_stage)
                    if not rule:
                        continue

                    if rule.requires_approval:
                        # Requires merchant manually triggering the progression; worker skips auto advancement
                        continue

                    # Calculate schedule time for next attempt
                    delay = self.dunning_policy.get_delay_for_stage(next_stage)
                    next_attempt_at = current_time + delay

                    ctx = ExecutionContext(
                        user_id="dunning_worker",
                        organization_id=receivable.organization_id.value,
                        role="SYSTEM"
                    )
                    cmd = AdvanceDunningStageCommand(
                        organization_id=receivable.organization_id.value,
                        receivable_id=receivable.id.value,
                        next_stage=next_stage.value,
                        next_attempt_at=next_attempt_at
                    )

                    res_handler = await handler.handle(cmd, ctx)
                    if res_handler.is_success:
                        # Schedule collection action record idempotently
                        idem_key = f"dunning-{receivable.id.value}-{next_stage.value}"
                        existing_action = await collection_action_repo.find_by_idempotency_key(
                            receivable.organization_id,
                            idem_key
                        )
                        if not existing_action:
                            action_id = str(uuid4())
                            action = CollectionAction.create(
                                id=CollectionActionId(action_id),
                                organization_id=receivable.organization_id,
                                receivable_id=receivable.id,
                                action_type=rule.action_type,
                                scheduled_at=current_time,
                                idempotency_key=idem_key,
                                created_at=current_time
                            )
                            await collection_action_repo.save(action)
                            processed_count += 1
                    else:
                        raise ValueError(f"Failed to advance dunning stage: {res_handler.error.message}")

                except Exception as e:
                    # Raise exception to rollback current transaction
                    raise e

            await session.commit()
            return processed_count
