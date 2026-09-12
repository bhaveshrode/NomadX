from enum import Enum
from typing import Optional

from ..shared.value_objects.value_object import ValueObject
from .value_objects import SettlementAmount, SettlementCurrency

class AdjustmentType(str, Enum):
    """Types of allowed settlement adjustments."""
    PROCESSING_FEE = "PROCESSING_FEE"
    REFUND_DEDUCTION = "REFUND_DEDUCTION"
    CHARGEBACK = "CHARGEBACK"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    OTHER_DEDUCTION = "OTHER_DEDUCTION"

class SettlementAdjustment(ValueObject):
    """Value object representing an explicit financial adjustment applied to the settlement."""
    def __init__(
        self,
        id: str,
        type: AdjustmentType,
        description: str,
        amount: SettlementAmount,
        currency: SettlementCurrency
    ):
        if not id or not isinstance(id, str) or id.strip() == "":
            raise ValueError("Adjustment ID cannot be empty.")
        if not isinstance(type, AdjustmentType):
            try:
                type = AdjustmentType(type)
            except ValueError:
                raise ValueError(f"Invalid adjustment type: {type}")
        if not description or not isinstance(description, str) or description.strip() == "":
            raise ValueError("Adjustment description cannot be empty.")
        if not isinstance(amount, SettlementAmount):
            raise ValueError("Adjustment amount must be a SettlementAmount.")
        if not isinstance(currency, SettlementCurrency):
            raise ValueError("Adjustment currency must be a SettlementCurrency.")

        super().__init__({
            "id": id.strip(),
            "type": type,
            "description": description.strip(),
            "amount": amount,
            "currency": currency
        })

    @property
    def id(self) -> str:
        return self.props["id"]

    @property
    def type(self) -> AdjustmentType:
        return self.props["type"]

    @property
    def description(self) -> str:
        return self.props["description"]

    @property
    def amount(self) -> SettlementAmount:
        return self.props["amount"]

    @property
    def currency(self) -> SettlementCurrency:
        return self.props["currency"]
