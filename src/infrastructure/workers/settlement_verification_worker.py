from sqlalchemy import select
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import SettlementDbModel
from ...infrastructure.database.repositories import PostgresSettlementRepository
from ...domain.settlement.value_objects import SettlementId, SettlementFailure, SettlementFailureCode
from ...domain.settlement.settlement import SettlementStatus
from ...application.settlement.verification import SettlementVerificationService
from ...infrastructure.settlement.interfaces import ISettlementVerificationAdapter

class SettlementVerificationWorker:
    """
    Background worker verifying submitted settlements on-chain.
    Ensures finality thresholds are met before completing settlements.
    """
    def __init__(
        self,
        verification_adapter: ISettlementVerificationAdapter,
        verification_service: SettlementVerificationService
    ) -> None:
        self.verification_adapter = verification_adapter
        self.verification_service = verification_service

    async def process_submitted_settlements(self) -> int:
        """
        Discovers settlements in SUBMITTED state, evaluates finality evidence,
        and transitions to CONFIRMED->COMPLETED or FAILED.
        """
        # 1. Fetch submitted settlement IDs
        async with session_manager.session() as session:
            stmt = select(SettlementDbModel.id).where(SettlementDbModel.status == "SUBMITTED")
            result = await session.execute(stmt)
            settlement_ids = result.scalars().all()

        processed_count = 0

        # 2. Process each settlement individually
        for s_id in settlement_ids:
            # Transaction 1: Acquire lock, fetch reference, release lock
            async with session_manager.session() as session:
                repo = PostgresSettlementRepository(session)
                settlement = await repo.get_by_id(SettlementId(s_id), lock=True)
                if not settlement or settlement.status != SettlementStatus.SUBMITTED:
                    continue

                network = settlement.network.value
                tx_ref = settlement.transaction_reference
                
                # Fetch properties for verify call later
                settlement_copy = settlement
                
                await session.commit() # release DB lock during external fetch

            if not tx_ref:
                continue

            # Fetch evidence from RPC adapter outside of DB transaction
            try:
                evidence = await self.verification_adapter.fetch_evidence(network, tx_ref)
            except Exception:
                # Temporary network/RPC error: remain SUBMITTED and retry verification later
                continue

            if not evidence:
                # Transaction missing on network: remain SUBMITTED to retry later
                continue

            # Evaluate evidence against verification policy
            verify_result = self.verification_service.verify(settlement_copy, evidence)

            # Transaction 2: Re-acquire row lock and apply results atomically
            async with session_manager.session() as session:
                repo = PostgresSettlementRepository(session)
                settlement = await repo.get_by_id(SettlementId(s_id), lock=True)
                if not settlement or settlement.status != SettlementStatus.SUBMITTED:
                    continue

                if verify_result.valid:
                    # Successful verification: transition to CONFIRMED and immediately to COMPLETED
                    settlement.confirm()
                    settlement.complete()
                    await repo.save(settlement)
                    await session.commit()
                    processed_count += 1
                else:
                    # Check if failure is ONLY insufficient confirmations
                    if verify_result.reasons == ["INSUFFICIENT_CONFIRMATIONS"]:
                        # Block depth not reached: remain SUBMITTED to poll again later
                        await session.commit()
                    else:
                        # Definitive failure: transition to FAILED state (permanent)
                        primary_reason = verify_result.reasons[0]
                        if primary_reason == "TRANSACTION_FAILED":
                            fail_code = SettlementFailureCode.TRANSACTION_REJECTED
                        elif primary_reason == "DESTINATION_MISMATCH":
                            fail_code = SettlementFailureCode.DESTINATION_INVALID
                        elif primary_reason == "AMOUNT_MISMATCH":
                            fail_code = SettlementFailureCode.INSUFFICIENT_FUNDS
                        else:
                            fail_code = SettlementFailureCode.UNKNOWN_ERROR

                        failure = SettlementFailure(
                            code=fail_code,
                            message=f"Verification failed: {', '.join(verify_result.reasons)}",
                            retryable=False
                        )
                        settlement.fail(failure)
                        await repo.save(settlement)
                        await session.commit()
                        processed_count += 1

        return processed_count
