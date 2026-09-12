import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject

class SettlementId(UniqueEntityID):
    """Identifier representing a Settlement's unique identity."""
    pass

class SettlementReference(ValueObject):
    """Value object representing a unique settlement instruction reference."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Settlement reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^SET-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Settlement reference must start with 'SET-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value

class SettlementBatchReference(ValueObject):
    """Value object representing a unique batch reference for reconciliation grouping."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Settlement batch reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^BATCH-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Settlement batch reference must start with 'BATCH-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value

class SettlementIdempotencyKey(ValueObject):
    """Value object representing a request-level idempotency key."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Idempotency key cannot be empty.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value

class SettlementCurrency(ValueObject):
    """Value object representing a settlement currency (e.g. USD, USDC, EUR)."""
    def __init__(self, code: str):
        if not code or not isinstance(code, str) or code.strip() == "":
            raise ValueError("Currency code cannot be empty.")
        clean_code = code.strip().upper()
        if len(clean_code) < 3 or len(clean_code) > 6:
            raise ValueError("Currency code must be between 3 and 6 characters.")
        if not re.match(r"^[A-Z0-9]+$", clean_code):
            raise ValueError("Currency code must be alphanumeric.")
        super().__init__({"code": clean_code})

    @property
    def code(self) -> str:
        return self.props["code"]

    def __str__(self) -> str:
        return self.code

class SettlementAmount(ValueObject):
    """Value object representing monetary value with exact Decimal precision for settlements."""
    def __init__(self, amount: Any):
        if amount is None:
            raise ValueError("Settlement amount cannot be null.")

        # Rejects floats to prevent rounding errors
        if isinstance(amount, float):
            raise TypeError("Floating point numbers are not allowed for SettlementAmount. Use Decimal, int, or str.")

        try:
            decimal_amount = Decimal(str(amount))
        except Exception:
            raise ValueError(f"Invalid monetary amount: {amount}")

        super().__init__({"amount": decimal_amount})

    @property
    def amount(self) -> Decimal:
        return self.props["amount"]

    def __add__(self, other: "SettlementAmount") -> "SettlementAmount":
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return SettlementAmount(self.amount + other.amount)

    def __sub__(self, other: "SettlementAmount") -> "SettlementAmount":
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return SettlementAmount(self.amount - other.amount)

    def __lt__(self, other: "SettlementAmount") -> bool:
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return self.amount < other.amount

    def __le__(self, other: "SettlementAmount") -> bool:
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return self.amount <= other.amount

    def __gt__(self, other: "SettlementAmount") -> bool:
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return self.amount > other.amount

    def __ge__(self, other: "SettlementAmount") -> bool:
        if not isinstance(other, SettlementAmount):
            return NotImplemented
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount:.2f}"

class SettlementPeriod(ValueObject):
    """Value object representing the calendar period covered by the settlement."""
    def __init__(self, start_date: datetime, end_date: datetime):
        if not isinstance(start_date, datetime) or not isinstance(end_date, datetime):
            raise ValueError("Start date and end date must be datetime objects.")
        if start_date > end_date:
            raise ValueError("Start date cannot be after end date.")
        super().__init__({"start_date": start_date, "end_date": end_date})

    @property
    def start_date(self) -> datetime:
        return self.props["start_date"]

    @property
    def end_date(self) -> datetime:
        return self.props["end_date"]

class SettlementLineReference(ValueObject):
    """Value object representing a unique reference for an individual settlement entry."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Settlement line reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^LINE-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Settlement line reference must start with 'LINE-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value

class MerchantReference(ValueObject):
    """Value object representing a unique reference for a merchant / organization."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Merchant reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^MERCH-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Merchant reference must start with 'MERCH-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value
