from datetime import datetime
from ..shared.specifications.specification import Specification
from .receivable import Receivable


class CanAllocatePayment(Specification[Receivable]):
    """Checks if a Receivable is in a valid state to accept payment allocations."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        from .receivable import ReceivableStatus
        return (
            candidate.outstanding_amount.amount > 0 
            and candidate.status not in (ReceivableStatus.SETTLED, ReceivableStatus.WRITTEN_OFF)
        )


class CanMarkOverdue(Specification[Receivable]):
    """Checks if a Receivable is eligible to be marked as overdue."""
    def __init__(self, current_time: datetime):
        self.current_time = current_time

    def is_satisfied_by(self, candidate: Receivable) -> bool:
        from .receivable import ReceivableStatus
        return (
            candidate.outstanding_amount.amount > 0
            and self.current_time > candidate.due_date
            and candidate.status not in (ReceivableStatus.OVERDUE, ReceivableStatus.SETTLED, ReceivableStatus.WRITTEN_OFF)
        )


class CanSettle(Specification[Receivable]):
    """Checks if a Receivable can transition to SETTLED state."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        from .receivable import ReceivableStatus
        return candidate.outstanding_amount.amount == 0 and candidate.status != ReceivableStatus.SETTLED


class CanWriteOff(Specification[Receivable]):
    """Checks if a Receivable can be written off."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        from .receivable import ReceivableStatus
        return (
            candidate.outstanding_amount.amount > 0
            and candidate.status not in (ReceivableStatus.SETTLED, ReceivableStatus.WRITTEN_OFF)
        )


class CanAdvanceDunningStage(Specification[Receivable]):
    """Checks if a Receivable can advance its dunning stage."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        from .receivable import ReceivableStatus
        from .dunning import DunningStage
        return (
            candidate.status not in (ReceivableStatus.SETTLED, ReceivableStatus.WRITTEN_OFF)
            and candidate.dunning_state.stage != DunningStage.COLLECTION_ESCALATION
            and not candidate.dunning_paused
        )


class IsOutstanding(Specification[Receivable]):
    """Checks if a Receivable has a positive outstanding balance."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        return candidate.outstanding_amount.amount > 0


class IsOverdue(Specification[Receivable]):
    """Checks if a Receivable is overdue relative to a timestamp."""
    def __init__(self, current_time: datetime):
        self.current_time = current_time

    def is_satisfied_by(self, candidate: Receivable) -> bool:
        return candidate.outstanding_amount.amount > 0 and self.current_time > candidate.due_date


class IsFullyAllocated(Specification[Receivable]):
    """Checks if the original amount is completely allocated (outstanding balance is 0)."""
    def is_satisfied_by(self, candidate: Receivable) -> bool:
        return candidate.outstanding_amount.amount == 0


# Keep aliases for backward compatibility with existing tests/code
ReceivableCanAcceptAllocation = CanAllocatePayment
ReceivableIsOutstanding = IsOutstanding
ReceivableIsOverdue = IsOverdue
ReceivableIsFullyPaid = IsFullyAllocated
