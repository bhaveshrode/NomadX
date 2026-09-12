import re
from enum import Enum
from typing import Any
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject


class SettlementId(UniqueEntityID):
    """Identifier representing a Settlement's unique identity."""
    pass


class SettlementReference(ValueObject):
    """Value object representing a unique merchant-facing settlement reference (e.g. SET-2026-000001)."""
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


class SettlementAsset(ValueObject):
    """Value object representing a settled currency or asset (e.g. USDC, EUR, USD)."""
    def __init__(self, code: str, type: str, decimals: int = 18):
        if not code or not isinstance(code, str) or code.strip() == "":
            raise ValueError("Asset code cannot be empty.")
        if not type or not isinstance(type, str) or type.strip() == "":
            raise ValueError("Asset type cannot be empty.")
        if decimals < 0:
            raise ValueError("Asset decimals cannot be negative.")
        super().__init__({
            "code": code.strip().upper(),
            "type": type.strip().upper(),
            "decimals": decimals
        })

    @property
    def code(self) -> str:
        return self.props["code"]

    @property
    def type(self) -> str:
        return self.props["type"]

    @property
    def decimals(self) -> int:
        return self.props["decimals"]


class SettlementNetwork(ValueObject):
    """Value object representing a settlement network (e.g. BASE, ETHEREUM, BANK)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Settlement network cannot be empty.")
        super().__init__({"value": value.strip().upper()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class SettlementDestinationType(str, Enum):
    BLOCKCHAIN_ADDRESS = "BLOCKCHAIN_ADDRESS"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    OTHER = "OTHER"


class SettlementDestination(ValueObject):
    """Value object representing a destination account/address on a settlement network."""
    def __init__(self, type: SettlementDestinationType, identifier: str, network: SettlementNetwork):
        if not isinstance(type, SettlementDestinationType):
            try:
                type = SettlementDestinationType(type)
            except ValueError:
                raise ValueError(f"Invalid destination type: {type}")
        if not identifier or not isinstance(identifier, str) or identifier.strip() == "":
            raise ValueError("Destination identifier cannot be empty.")
        if not isinstance(network, SettlementNetwork):
            raise ValueError("Destination network must be a SettlementNetwork.")

        clean_id = identifier.strip()
        if type == SettlementDestinationType.BLOCKCHAIN_ADDRESS:
            if not clean_id.startswith("0x") or len(clean_id) != 42:
                raise ValueError("Blockchain address destination must start with '0x' and be 42 characters long.")

        super().__init__({
            "type": type,
            "identifier": clean_id,
            "network": network
        })

    @property
    def type(self) -> SettlementDestinationType:
        return self.props["type"]

    @property
    def identifier(self) -> str:
        return self.props["identifier"]

    @property
    def network(self) -> SettlementNetwork:
        return self.props["network"]


class SettlementMethod(str, Enum):
    DIGITAL_ASSET = "DIGITAL_ASSET"
    BANK_TRANSFER = "BANK_TRANSFER"
    OTHER = "OTHER"


class SettlementSourceType(str, Enum):
    PAYMENT = "PAYMENT"
    OTHER = "OTHER"


class SettlementSource(ValueObject):
    """Value object representing the source financial event triggering this settlement."""
    def __init__(self, type: SettlementSourceType, payment_id: str):
        if not isinstance(type, SettlementSourceType):
            try:
                type = SettlementSourceType(type)
            except ValueError:
                raise ValueError(f"Invalid source type: {type}")
        if not payment_id or not isinstance(payment_id, str) or payment_id.strip() == "":
            raise ValueError("Source payment_id cannot be empty.")
        super().__init__({
            "type": type,
            "payment_id": payment_id.strip()
        })

    @property
    def type(self) -> SettlementSourceType:
        return self.props["type"]

    @property
    def payment_id(self) -> str:
        return self.props["payment_id"]


class SettlementFailureCode(str, Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    DESTINATION_INVALID = "DESTINATION_INVALID"
    TRANSACTION_REJECTED = "TRANSACTION_REJECTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class SettlementFailure(ValueObject):
    """Value object representing failure reasons for failed or retried settlements."""
    def __init__(self, code: SettlementFailureCode, message: str, retryable: bool = False):
        if not isinstance(code, SettlementFailureCode):
            try:
                code = SettlementFailureCode(code)
            except ValueError:
                raise ValueError(f"Invalid failure code: {code}")
        super().__init__({
            "code": code,
            "message": message,
            "retryable": retryable
        })

    @property
    def code(self) -> SettlementFailureCode:
        return self.props["code"]

    @property
    def message(self) -> str:
        return self.props["message"]

    @property
    def retryable(self) -> bool:
        return self.props["retryable"]


class SettlementIdempotencyKey(ValueObject):
    """Value object representing a request-level idempotency key for settlements."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Idempotency key cannot be empty.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value
