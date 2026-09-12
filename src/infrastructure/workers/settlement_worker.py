from sqlalchemy import select
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import SettlementDbModel
from ...infrastructure.database.repositories import PostgresSettlementRepository
from ...domain.settlement.value_objects import SettlementId, SettlementFailure, SettlementFailureCode
from ...infrastructure.settlement.models import SettlementExecutionRequest, SettlementExecutionResult
from ...infrastructure.settlement.interfaces import ISettlementExecutionAdapter

class SettlementWorker:
    """
    Background worker that queries, locks, and executes PROCESSING settlements
    via a configured ISettlementExecutionAdapter, ensuring transactional safety
    and execution idempotency.
    """
    def __init__(self, execution_adapter: ISettlementExecutionAdapter) -> None:
        self.execution_adapter = execution_adapter

    async def process_pending_settlements(self) -> int:
        """
        Queries all settlements in PROCESSING state, releases DB lock during external
        execution, and commits the final status (SUBMITTED/FAILED) atomically.
        """
        # 1. Fetch processing settlement IDs
        async with session_manager.session() as session:
            stmt = select(SettlementDbModel.id).where(SettlementDbModel.status == "PROCESSING")
            result = await session.execute(stmt)
            settlement_ids = result.scalars().all()

        processed_count = 0

        # 2. Iterate and process each settlement individually
        for s_id in settlement_ids:
            # Transaction 1: Acquire row lock, fetch properties, release lock immediately
            async with session_manager.session() as session:
                repo = PostgresSettlementRepository(session)
                settlement = await repo.get_by_id(SettlementId(s_id), lock=True)
                if not settlement or settlement.status.value != "PROCESSING":
                    # State changed concurrently, skip
                    continue

                request = SettlementExecutionRequest(
                    settlement_id=settlement.id.value,
                    amount=settlement.amount.amount,
                    asset_code=settlement.asset.code,
                    asset_decimals=settlement.asset.decimals,
                    network=settlement.network.value,
                    destination_identifier=settlement.destination.identifier,
                    idempotency_key=settlement.idempotency_key.value
                )
                # Commit releases the pessimistic DB lock (with_for_update)
                await session.commit()

            # External Execution: Call adapter outside the DB transaction boundary to avoid holding DB connections open
            try:
                exec_result = await self.execution_adapter.execute(request)
            except Exception as e:
                # System or RPC level errors are considered retryable failures
                exec_result = SettlementExecutionResult(
                    is_success=False,
                    failure_code="PROVIDER_ERROR",
                    failure_message=str(e),
                    failure_retryable=True
                )

            # Transaction 2: Re-acquire row lock, persist execution results, generate outbox events
            async with session_manager.session() as session:
                repo = PostgresSettlementRepository(session)
                settlement = await repo.get_by_id(SettlementId(s_id), lock=True)
                if not settlement or settlement.status.value != "PROCESSING":
                    # Settlement was mutated concurrently while worker was executing, skip
                    continue

                if exec_result.is_success:
                    settlement.submit(exec_result.transaction_reference)
                else:
                    # Map failure code to SettlementFailureCode enum
                    try:
                        code_enum = SettlementFailureCode(exec_result.failure_code)
                    except ValueError:
                        code_enum = SettlementFailureCode.UNKNOWN_ERROR

                    failure = SettlementFailure(
                        code=code_enum,
                        message=exec_result.failure_message or "Execution failed without a detailed message.",
                        retryable=exec_result.failure_retryable
                    )
                    settlement.fail(failure)

                await repo.save(settlement)
                await session.commit()
                processed_count += 1

        return processed_count
