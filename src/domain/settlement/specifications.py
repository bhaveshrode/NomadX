from typing import Any
from ..shared.specifications.specification import Specification
from .value_objects import SettlementMethod, SettlementDestinationType


class SettlementCanBeCreated(Specification[Any]):
    """
    Specification verifying if a Settlement can be created.
    Requires positive amount and matching destination types.
    """
    def is_satisfied_by(self, candidate: Any) -> bool:
        # candidate is a dict of creation parameters
        amount = candidate.get("amount")
        if amount is None or amount.amount <= 0:
            return False

        method = candidate.get("method")
        dest = candidate.get("destination")
        if not method or not dest:
            return False

        if method == SettlementMethod.DIGITAL_ASSET:
            return dest.type == SettlementDestinationType.BLOCKCHAIN_ADDRESS
        elif method == SettlementMethod.BANK_TRANSFER:
            return dest.type == SettlementDestinationType.BANK_ACCOUNT
        return True


class SettlementCanBeSubmitted(Specification["Settlement"]):
    """Specification verifying if a Settlement can be submitted to external rail."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status == SettlementStatus.PROCESSING


class SettlementCanBeConfirmed(Specification["Settlement"]):
    """Specification verifying if a Settlement can be confirmed."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status == SettlementStatus.SUBMITTED


class SettlementCanBeCompleted(Specification["Settlement"]):
    """Specification verifying if a Settlement can transition to completed."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status in (SettlementStatus.CONFIRMED, SettlementStatus.RETRYING)


class SettlementCanBeCancelled(Specification["Settlement"]):
    """Specification verifying if a Settlement can be cancelled."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status == SettlementStatus.PENDING


class SettlementCanBeRetried(Specification["Settlement"]):
    """Specification verifying if a Settlement can be retried after failure."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        if candidate.status != SettlementStatus.FAILED:
            return False
        return candidate.failure is not None and candidate.failure.retryable
