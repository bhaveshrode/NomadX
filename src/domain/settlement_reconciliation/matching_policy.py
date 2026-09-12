from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from typing import Any

from .settlement import Settlement, SettlementEntry
from .source_transaction import SourceTransaction


class MatchingPolicy(ABC):
    """
    Base matching policy class to evaluate if a settlement entry matches a source transaction.
    """
    @abstractmethod
    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        pass


class ExactReferenceMatchingPolicy(MatchingPolicy):
    """Matches if entry transaction reference exactly matches source transaction reference."""
    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        return (
            entry.source_transaction_reference.strip().lower() == 
            transaction.transaction_reference.strip().lower()
        )


class AmountCurrencyMatchingPolicy(MatchingPolicy):
    """Matches if amounts and currency match exactly."""
    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        return (
            entry.gross_amount.amount == transaction.amount and
            entry.currency.code.strip().upper() == transaction.currency_code.strip().upper()
        )


class MerchantMatchingPolicy(MatchingPolicy):
    """Matches if the organization/merchant ownership is consistent."""
    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        return settlement.organization_id.value == transaction.organization_id


class PeriodMatchingPolicy(MatchingPolicy):
    """Matches if the source transaction occurs within the settlement's period boundary."""
    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        # Support timezone naive comparisons cleanly
        t_time = transaction.observed_at.replace(tzinfo=None)
        p_start = settlement.period.start_date.replace(tzinfo=None)
        p_end = settlement.period.end_date.replace(tzinfo=None)
        return p_start <= t_time <= p_end


class ToleranceMatchingPolicy(MatchingPolicy):
    """Allows amount matching with a configurable monetary variance threshold (e.g. 0.05)."""
    def __init__(self, tolerance: Decimal = Decimal("0.05")):
        self._tolerance = tolerance

    def matches(self, entry: SettlementEntry, transaction: SourceTransaction, settlement: Settlement) -> bool:
        # Check currency matches first
        if entry.currency.code.strip().upper() != transaction.currency_code.strip().upper():
            return False
        # Absolute difference is within tolerance
        difference = abs(entry.net_amount.amount - transaction.net_amount)
        return difference <= self._tolerance
