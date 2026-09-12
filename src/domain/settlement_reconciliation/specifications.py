from typing import Any
from ..shared.specifications.specification import Specification

class CanAddSettlementEntry(Specification[Any]):
    """Specification verifying if an entry can be added to the Settlement aggregate."""
    def is_satisfied_by(self, candidate: Any) -> bool:
        # candidate is a dict of {"settlement": Settlement, "entry": SettlementEntry}
        settlement = candidate.get("settlement")
        entry = candidate.get("entry")
        if not settlement or not entry:
            return False

        from .settlement import SettlementStatus
        if settlement.status not in (SettlementStatus.DRAFT, SettlementStatus.OPEN):
            return False

        # Currency consistency
        if settlement.currency.code != entry.currency.code:
            return False

        # Duplicate checking
        for existing in settlement.entries:
            if existing.source_transaction_reference == entry.source_transaction_reference:
                return False

        return True

class CanReconcileSettlement(Specification["Settlement"]):
    """Specification verifying if reconciliation can be executed on a Settlement."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status == SettlementStatus.RECONCILING

class CanCompleteSettlement(Specification["Settlement"]):
    """Specification verifying if a Settlement can be completed."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        if candidate.status != SettlementStatus.RECONCILED:
            return False
        
        # Check unresolved discrepancies
        if hasattr(candidate, "reconciliation_result") and candidate.reconciliation_result:
            for discrepancy in candidate.reconciliation_result.discrepancies:
                if not discrepancy.resolved:
                    return False
        return True

class CanCancelSettlement(Specification["Settlement"]):
    """Specification verifying if a Settlement can be cancelled."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        from .settlement import SettlementStatus
        return candidate.status not in (SettlementStatus.COMPLETED, SettlementStatus.CANCELLED)

class CanApplyAdjustment(Specification[Any]):
    """Specification verifying if a financial adjustment can be applied."""
    def is_satisfied_by(self, candidate: Any) -> bool:
        # candidate is a dict of {"settlement": Settlement, "adjustment": SettlementAdjustment}
        settlement = candidate.get("settlement")
        adjustment = candidate.get("adjustment")
        if not settlement or not adjustment:
            return False

        from .settlement import SettlementStatus
        if settlement.status not in (SettlementStatus.DRAFT, SettlementStatus.OPEN):
            return False

        if settlement.currency.code != adjustment.currency.code:
            return False

        return True

class IsSettlementBalanced(Specification["Settlement"]):
    """Specification verifying if the gross/fee/adjustment/net amounts balance mathematically."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        # gross_amount - fees_amount - adjustments_amount == net_amount
        expected_net = candidate.gross_amount.amount - candidate.fees_amount.amount - candidate.adjustments_amount.amount
        return candidate.net_amount.amount == expected_net

class IsCurrencyConsistent(Specification["Settlement"]):
    """Specification verifying if all sub-elements share the same currency code."""
    def is_satisfied_by(self, candidate: "Settlement") -> bool:
        currency_code = candidate.currency.code
        for entry in candidate.entries:
            if entry.currency.code != currency_code:
                return False
        for adjustment in candidate.adjustments:
            if adjustment.currency.code != currency_code:
                return False
        return True
