import re
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject
from ..invoice.value_objects import Money, Currency
from .aging import AgingBucket


class ReceivableId(UniqueEntityID):
    """Unique identity representing an Accounts Receivable record."""
    pass


class CustomerId(UniqueEntityID):
    """Unique identity representing a Customer."""
    pass


class InvoiceId(UniqueEntityID):
    """Unique identity representing an Invoice."""
    pass


class ReceivableReference(ValueObject):
    """Value object representing a unique merchant-facing accounts receivable reference (e.g. AR-1001)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Receivable reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^AR-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Receivable reference must start with 'AR-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class CustomerReference(ValueObject):
    """Value object representing a customer reference."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Customer reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^CUST-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Customer reference must start with 'CUST-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class InvoiceReference(ValueObject):
    """Value object representing an invoice reference."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Invoice reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^INV-[A-Za-z0-9\-_]+$", clean_val) and not re.match(r"^[A-Za-z0-9\-_/]+$", clean_val):
            raise ValueError("Invoice reference must start with 'INV-' or follow the default invoice number format.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class ReceivableIdempotencyKey(ValueObject):
    """Value object representing a tenant-scoped idempotency key."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Idempotency key cannot be empty.")
        clean_val = value.strip()
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value
