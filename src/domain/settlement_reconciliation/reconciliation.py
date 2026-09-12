from datetime import datetime
from enum import Enum
from typing import List, Optional

from ..shared.value_objects.value_object import ValueObject
from .value_objects import SettlementLineReference, SettlementAmount

class ReconciliationStatus(str, Enum):
    """Reconciliation state options."""
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
    UNMATCHED = "UNMATCHED"
    DISCREPANCY = "DISCREPANCY"

class ReconciliationDifference(ValueObject):
    """Value object representing the variance/difference value found during reconciliation."""
    def __init__(self, amount: SettlementAmount):
        if not isinstance(amount, SettlementAmount):
            raise ValueError("Difference amount must be a SettlementAmount.")
        super().__init__({"amount": amount})

    @property
    def amount(self) -> SettlementAmount:
        return self.props["amount"]

    def __str__(self) -> str:
        return str(self.amount)

class ReconciliationDiscrepancy(ValueObject):
    """Value object capturing mismatch details of a detected discrepancy."""
    def __init__(
        self,
        entry_reference: SettlementLineReference,
        discrepancy_type: str,
        expected_amount: SettlementAmount,
        actual_amount: SettlementAmount,
        description: str,
        resolved: bool = False
    ):
        if not isinstance(entry_reference, SettlementLineReference):
            raise ValueError("Entry reference must be a SettlementLineReference.")
        if not discrepancy_type or not isinstance(discrepancy_type, str) or discrepancy_type.strip() == "":
            raise ValueError("Discrepancy type cannot be empty.")
        if not isinstance(expected_amount, SettlementAmount) or not isinstance(actual_amount, SettlementAmount):
            raise ValueError("Expected and actual amounts must be SettlementAmount objects.")
        if not description or not isinstance(description, str) or description.strip() == "":
            raise ValueError("Discrepancy description cannot be empty.")

        difference_val = expected_amount.amount - actual_amount.amount
        difference = ReconciliationDifference(SettlementAmount(difference_val))

        super().__init__({
            "entry_reference": entry_reference,
            "discrepancy_type": discrepancy_type.strip(),
            "expected_amount": expected_amount,
            "actual_amount": actual_amount,
            "difference": difference,
            "description": description.strip(),
            "resolved": resolved
        })

    @property
    def entry_reference(self) -> SettlementLineReference:
        return self.props["entry_reference"]

    @property
    def discrepancy_type(self) -> str:
        return self.props["discrepancy_type"]

    @property
    def expected_amount(self) -> SettlementAmount:
        return self.props["expected_amount"]

    @property
    def actual_amount(self) -> SettlementAmount:
        return self.props["actual_amount"]

    @property
    def difference(self) -> ReconciliationDifference:
        return self.props["difference"]

    @property
    def description(self) -> str:
        return self.props["description"]

    @property
    def resolved(self) -> bool:
        return self.props["resolved"]

    def resolve(self) -> "ReconciliationDiscrepancy":
        """Returns a resolved copy of this discrepancy."""
        return ReconciliationDiscrepancy(
            entry_reference=self.entry_reference,
            discrepancy_type=self.discrepancy_type,
            expected_amount=self.expected_amount,
            actual_amount=self.actual_amount,
            description=self.description,
            resolved=True
        )

class ReconciliationResult(ValueObject):
    """Value object capturing the final outputs and artifacts of a reconciliation operation."""
    def __init__(
        self,
        status: ReconciliationStatus,
        discrepancies: List[ReconciliationDiscrepancy],
        verified_at: datetime,
        description: str
    ):
        if not isinstance(status, ReconciliationStatus):
            try:
                status = ReconciliationStatus(status)
            except ValueError:
                raise ValueError(f"Invalid reconciliation status: {status}")
        if not isinstance(discrepancies, list) or not all(isinstance(d, ReconciliationDiscrepancy) for d in discrepancies):
            raise ValueError("Discrepancies must be a list of ReconciliationDiscrepancy objects.")
        if not isinstance(verified_at, datetime):
            raise ValueError("Verification timestamp must be a datetime object.")
        if not description or not isinstance(description, str) or description.strip() == "":
            raise ValueError("Description cannot be empty.")

        super().__init__({
            "status": status,
            "discrepancies": list(discrepancies),
            "verified_at": verified_at,
            "description": description.strip()
        })

    @property
    def status(self) -> ReconciliationStatus:
        return self.props["status"]

    @property
    def discrepancies(self) -> List[ReconciliationDiscrepancy]:
        return self.props["discrepancies"]

    @property
    def verified_at(self) -> datetime:
        return self.props["verified_at"]

    @property
    def description(self) -> str:
        return self.props["description"]
