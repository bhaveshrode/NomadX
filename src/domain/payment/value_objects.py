import re
from enum import Enum
from typing import Optional
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject


class PaymentIntentId(UniqueEntityID):
    """Identifier representing a PaymentIntent's unique identity."""
    pass


class PaymentId(UniqueEntityID):
    """Identifier representing a Payment's unique identity."""
    pass


class PaymentAllocationId(UniqueEntityID):
    """Identifier representing a PaymentAllocation's unique identity."""
    pass


class PaymentMethod(str, Enum):
    """Supported payment methods/rails."""
    BANK_TRANSFER = "BANK_TRANSFER"
    CARD = "CARD"
    DIGITAL_ASSET = "DIGITAL_ASSET"
    OTHER = "OTHER"


class PaymentReference(ValueObject):
    """Value object representing a unique payment-matching reference (e.g. PAY-2026-XXXX)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Payment reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Payment reference can only contain letters, numbers, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class PaymentAsset(ValueObject):
    """
    Value object representing a payment asset (fiat or digital) independent of network.
    E.g. USDC, EUR, BTC.
    """
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


class PaymentNetwork(ValueObject):
    """
    Value object representing a payment network or network layer.
    E.g. ETHEREUM, BASE, SEPA, POLYGON.
    """
    def __init__(self, code: str, type: str):
        if not code or not isinstance(code, str) or code.strip() == "":
            raise ValueError("Network code cannot be empty.")
        if not type or not isinstance(type, str) or type.strip() == "":
            raise ValueError("Network type cannot be empty.")
            
        super().__init__({
            "code": code.strip().upper(),
            "type": type.strip().upper()
        })

    @property
    def code(self) -> str:
        return self.props["code"]

    @property
    def type(self) -> str:
        return self.props["type"]


class PaymentDestination(ValueObject):
    """
    Value object representing where funds should be sent.
    E.g. blockchain address, IBAN, bank account.
    """
    def __init__(self, type: str, identifier: str, network: Optional[str] = None):
        if not type or not isinstance(type, str) or type.strip() == "":
            raise ValueError("Destination type cannot be empty.")
        if not identifier or not isinstance(identifier, str) or identifier.strip() == "":
            raise ValueError("Destination identifier cannot be empty.")
            
        super().__init__({
            "type": type.strip().upper(),
            "identifier": identifier.strip(),
            "network": network.strip().upper() if network else None
        })

    @property
    def type(self) -> str:
        return self.props["type"]

    @property
    def identifier(self) -> str:
        return self.props["identifier"]

    @property
    def network(self) -> Optional[str]:
        return self.props["network"]
