from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ...domain.shared.result.result import Result, ResultError
from ...domain.shared.clock.clock import Clock
from ...domain.organization.value_objects import OrganizationId
from ...domain.receivable.repositories import IReceivableRepository
from ...domain.receivable.policies import CollectionPolicy
from ...presentation.api.dependencies.context import ExecutionContext


@dataclass(frozen=True)
class EvaluateReceivableAgingCommand:
    """Command to trigger Accounts Receivable aging evaluation for an organization."""
    organization_id: str


class EvaluateReceivableAgingHandler:
    """Coordinates overdue detection and aging buckets transitions for an organization's receivables."""
    def __init__(self, receivable_repo: IReceivableRepository, policy: Optional[CollectionPolicy] = None):
        self._receivable_repo = receivable_repo
        self._policy = policy or CollectionPolicy()

    async def handle(self, command: EvaluateReceivableAgingCommand, ctx: ExecutionContext) -> Result[int]:
        # Tenant boundary check
        if command.organization_id != ctx.organization_id:
            return Result.fail(ResultError.unauthorized("Context tenant mismatch: organization ID does not match execution context."))

        current_time = Clock.now()
        candidates = await self._receivable_repo.find_outstanding_candidates(
            OrganizationId(command.organization_id),
            current_time
        )

        evaluated_count = 0
        for candidate in candidates:
            # Pessimistic locking of the receivable row
            receivable = await self._receivable_repo.get_by_id(candidate.id, lock=True)
            if not receivable:
                continue

            old_collection_status = receivable.collection_status

            # Run domain aging calculations & check overdue
            receivable.update_aging_and_detect_threshold(Clock)
            receivable.check_overdue(current_time)

            # Check collection attention policies
            days_overdue = receivable.calculate_days_overdue(current_time)
            if self._policy.requires_attention(days_overdue):
                receivable.trigger_collection_attention(current_time)

            # Save aggregate & outbox events if changed or events added
            if (
                receivable.collection_status != old_collection_status
                or len(receivable.domain_events) > 0
            ):
                await self._receivable_repo.save(receivable)
                evaluated_count += 1

        return Result.ok(evaluated_count)
