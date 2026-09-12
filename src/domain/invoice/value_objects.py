from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject


class InvoiceId(UniqueEntityID):
    """Identifier representing an Invoice's unique identity."""
    pass


class CustomerId(UniqueEntityID):
    """Identifier representing a Customer's unique identity."""
    pass


class InvoiceNumber(ValueObject):
    """Value object representing a human-readable merchant-assigned invoice number."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Invoice number cannot be empty.")
        clean_val = value.strip()
        # Allow alphanumeric, hyphens, and slashes
        import re
        if not re.match(r"^[A-Za-z0-9\-_/]+$", clean_val):
            raise ValueError(
                "Invoice number can only contain letters, numbers, hyphens, underscores, and slashes."
            )
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class CurrencyType(str, Enum):
    FIAT = "FIAT"
    DIGITAL_ASSET = "DIGITAL_ASSET"


class Currency(ValueObject):
    """Value object representing a Currency (FIAT or DIGITAL_ASSET)."""
    def __init__(self, code: str, type: CurrencyType):
        if not code or not isinstance(code, str) or code.strip() == "":
            raise ValueError("Currency code cannot be empty.")
        clean_code = code.strip().upper()
        if len(clean_code) < 3 or len(clean_code) > 6:
            raise ValueError("Currency code must be between 3 and 6 characters.")
        if not isinstance(type, CurrencyType):
            try:
                type = CurrencyType(type)
            except ValueError:
                raise ValueError(f"Invalid currency type: {type}")
        super().__init__({"code": clean_code, "type": type})

    @property
    def code(self) -> str:
        return self.props["code"]

    @property
    def type(self) -> CurrencyType:
        return self.props["type"]

    def __str__(self) -> str:
        return self.code


class Money(ValueObject):
    """Value object representing monetary value with exact Decimal precision."""
    def __init__(self, amount: Any, currency: Currency):
        if amount is None:
            raise ValueError("Money amount cannot be null.")
        if not isinstance(currency, Currency):
            raise ValueError("Money currency must be a Currency value object.")

        # Block floats to prevent rounding errors
        if isinstance(amount, float):
            raise TypeError("Floating point numbers are not allowed for Money amount. Use Decimal, int, or str.")

        try:
            decimal_amount = Decimal(str(amount))
        except Exception:
            raise ValueError(f"Invalid monetary amount: {amount}")

        super().__init__({"amount": decimal_amount, "currency": currency})

    @property
    def amount(self) -> Decimal:
        return self.props["amount"]

    @property
    def currency(self) -> Currency:
        return self.props["currency"]

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot add money of different currencies.")
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot subtract money of different currencies.")
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, other: Any) -> "Money":
        if isinstance(other, float):
            raise TypeError("Cannot multiply Money by a float to avoid precision issues. Use Decimal or int.")
        if isinstance(other, (int, Decimal)):
            return Money(self.amount * Decimal(other), self.currency)
        return NotImplemented

    def __rmul__(self, other: Any) -> "Money":
        return self.__mul__(other)

    def __lt__(self, other: "Money") -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare money of different currencies.")
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare money of different currencies.")
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare money of different currencies.")
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare money of different currencies.")
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.currency.code} {self.amount:.2f}"


class BillingSnapshot(ValueObject):
    """Value object representing a historical immutable snapshot of customer billing details."""
    def __init__(
        self,
        legal_name: str,
        email: str,
        address: str,
        country: str,
        tax_identifier: Optional[str] = None
    ):
        if not legal_name or not isinstance(legal_name, str) or legal_name.strip() == "":
            raise ValueError("Billing snapshot legal name cannot be empty.")
        if not email or not isinstance(email, str) or email.strip() == "":
            raise ValueError("Billing snapshot email cannot be empty.")
        if not address or not isinstance(address, str) or address.strip() == "":
            raise ValueError("Billing snapshot address cannot be empty.")
        if not country or not isinstance(country, str) or country.strip() == "":
            raise ValueError("Billing snapshot country cannot be empty.")

        super().__init__({
            "legal_name": legal_name.strip(),
            "email": email.strip(),
            "address": address.strip(),
            "country": country.strip(),
            "tax_identifier": tax_identifier.strip() if tax_identifier else None
        })

    @property
    def legal_name(self) -> str:
        return self.props["legal_name"]

    @property
    def email(self) -> str:
        return self.props["email"]

    @property
    def address(self) -> str:
        return self.props["address"]

    @property
    def country(self) -> str:
        return self.props["country"]

    @property
    def tax_identifier(self) -> Optional[str]:
        return self.props["tax_identifier"]


class InvoiceOriginType(str, Enum):
    NATIVE = "NATIVE"
    EXTERNAL = "EXTERNAL"


class InvoiceOrigin(ValueObject):
    """Value object representing the origin of an invoice (NomadX-native or ERP-ingested)."""
    def __init__(
        self,
        type: InvoiceOriginType,
        source_system: Optional[str] = None,
        external_invoice_id: Optional[str] = None
    ):
        if not isinstance(type, InvoiceOriginType):
            try:
                type = InvoiceOriginType(type)
            except ValueError:
                raise ValueError(f"Invalid origin type: {type}")

        if type == InvoiceOriginType.EXTERNAL:
            if not source_system or not isinstance(source_system, str) or source_system.strip() == "":
                raise ValueError("Source system is required for external invoices.")
            if not external_invoice_id or not isinstance(external_invoice_id, str) or external_invoice_id.strip() == "":
                raise ValueError("External invoice ID is required for external invoices.")

        super().__init__({
            "type": type,
            "source_system": source_system.strip() if source_system else None,
            "external_invoice_id": external_invoice_id.strip() if external_invoice_id else None
        })

    @property
    def type(self) -> InvoiceOriginType:
        return self.props["type"]

    @property
    def source_system(self) -> Optional[str]:
        return self.props["source_system"]

    @property
    def external_invoice_id(self) -> Optional[str]:
        return self.props["external_invoice_id"]


class TaxLine(ValueObject):
    """Value object representing a tax component applied to the invoice."""
    def __init__(
        self,
        code: str,
        name: str,
        rate: Decimal,
        taxable_amount: Money,
        amount: Money
    ):
        if not code or not isinstance(code, str) or code.strip() == "":
            raise ValueError("Tax code cannot be empty.")
        if not name or not isinstance(name, str) or name.strip() == "":
            raise ValueError("Tax name cannot be empty.")
        if rate is None or not isinstance(rate, Decimal):
            raise ValueError("Tax rate must be a Decimal.")
        if not isinstance(taxable_amount, Money) or not isinstance(amount, Money):
            raise ValueError("Taxable amount and tax amount must be Money objects.")
        if taxable_amount.currency != amount.currency:
            raise ValueError("Taxable amount and tax amount currencies must match.")

        super().__init__({
            "code": code.strip().upper(),
            "name": name.strip(),
            "rate": rate,
            "taxable_amount": taxable_amount,
            "amount": amount
        })

    @property
    def code(self) -> str:
        return self.props["code"]

    @property
    def name(self) -> str:
        return self.props["name"]

    @property
    def rate(self) -> Decimal:
        return self.props["rate"]

    @property
    def taxable_amount(self) -> Money:
        return self.props["taxable_amount"]

    @property
    def amount(self) -> Money:
        return self.props["amount"]


class AdjustmentType(str, Enum):
    DISCOUNT = "DISCOUNT"
    CHARGE = "CHARGE"


class Adjustment(ValueObject):
    """Value object representing an invoice-level adjustment (discount or charge)."""
    def __init__(
        self,
        type: AdjustmentType,
        description: str,
        amount: Money,
        rate: Optional[Decimal] = None
    ):
        if not isinstance(type, AdjustmentType):
            try:
                type = AdjustmentType(type)
            except ValueError:
                raise ValueError(f"Invalid adjustment type: {type}")
        if not description or not isinstance(description, str) or description.strip() == "":
            raise ValueError("Adjustment description cannot be empty.")
        if not isinstance(amount, Money):
            raise ValueError("Adjustment amount must be a Money object.")
        if rate is not None and not isinstance(rate, Decimal):
            raise ValueError("Adjustment rate must be a Decimal.")

        super().__init__({
            "type": type,
            "description": description.strip(),
            "amount": amount,
            "rate": rate
        })

    @property
    def type(self) -> AdjustmentType:
        return self.props["type"]

    @property
    def description(self) -> str:
        return self.props["description"]

    @property
    def amount(self) -> Money:
        return self.props["amount"]

    @property
    def rate(self) -> Optional[Decimal]:
        return self.props["rate"]


class PaymentTermsType(str, Enum):
    DUE_ON_RECEIPT = "DUE_ON_RECEIPT"
    NET_7 = "NET_7"
    NET_15 = "NET_15"
    NET_30 = "NET_30"
    NET_60 = "NET_60"
    CUSTOM = "CUSTOM"


class PaymentTerms(ValueObject):
    """Value object representing invoice payment terms."""
    def __init__(
        self,
        terms_type: PaymentTermsType,
        due_at: Optional[datetime] = None,
        notes: Optional[str] = None
    ):
        if not isinstance(terms_type, PaymentTermsType):
            try:
                terms_type = PaymentTermsType(terms_type)
            except ValueError:
                raise ValueError(f"Invalid payment terms type: {terms_type}")

        if terms_type == PaymentTermsType.CUSTOM and due_at is None:
            raise ValueError("Due date must be specified for custom payment terms.")

        super().__init__({
            "terms_type": terms_type,
            "due_at": due_at,
            "notes": notes.strip() if notes else None
        })

    @property
    def terms_type(self) -> PaymentTermsType:
        return self.props["terms_type"]

    @property
    def due_at(self) -> Optional[datetime]:
        return self.props["due_at"]

    @property
    def notes(self) -> Optional[str]:
        return self.props["notes"]

    def derive_due_date(self, issue_date: datetime) -> datetime:
        """Derives the due date based on payment terms and the invoice issue date."""
        if not isinstance(issue_date, datetime):
            raise ValueError("Issue date must be a datetime object.")

        if self.terms_type == PaymentTermsType.DUE_ON_RECEIPT:
            return issue_date
        elif self.terms_type == PaymentTermsType.NET_7:
            return issue_date + timedelta(days=7)
        elif self.terms_type == PaymentTermsType.NET_15:
            return issue_date + timedelta(days=15)
        elif self.terms_type == PaymentTermsType.NET_30:
            return issue_date + timedelta(days=30)
        elif self.terms_type == PaymentTermsType.NET_60:
            return issue_date + timedelta(days=60)
        elif self.terms_type == PaymentTermsType.CUSTOM:
            return self.due_at  # type: ignore


class InvoiceTotals(ValueObject):
    """Value object holding invoice totals and validating the total formula constraint."""
    def __init__(
        self,
        subtotal: Money,
        discount_total: Money,
        charge_total: Money,
        tax_total: Money,
        grand_total: Money
    ):
        # Validate that all total items are Money objects
        if not (isinstance(subtotal, Money) and
                isinstance(discount_total, Money) and
                isinstance(charge_total, Money) and
                isinstance(tax_total, Money) and
                isinstance(grand_total, Money)):
            raise ValueError("All totals must be Money value objects.")

        # Ensure currency match
        currency = subtotal.currency
        if not (discount_total.currency == currency and
                charge_total.currency == currency and
                tax_total.currency == currency and
                grand_total.currency == currency):
            raise ValueError("All invoice totals must be in the same currency.")

        # Enforce exact calculation rule: grand_total = subtotal - discount_total + charge_total + tax_total
        expected_grand_total = subtotal.amount - discount_total.amount + charge_total.amount + tax_total.amount
        if grand_total.amount != expected_grand_total:
            raise ValueError(
                f"Totals invariant violated: grand_total ({grand_total.amount}) must equal "
                f"subtotal ({subtotal.amount}) - discount_total ({discount_total.amount}) + "
                f"charge_total ({charge_total.amount}) + tax_total ({tax_total.amount}). "
                f"Expected: {expected_grand_total}"
            )

        super().__init__({
            "subtotal": subtotal,
            "discount_total": discount_total,
            "charge_total": charge_total,
            "tax_total": tax_total,
            "grand_total": grand_total
        })

    @property
    def subtotal(self) -> Money:
        return self.props["subtotal"]

    @property
    def discount_total(self) -> Money:
        return self.props["discount_total"]

    @property
    def charge_total(self) -> Money:
        return self.props["charge_total"]

    @property
    def tax_total(self) -> Money:
        return self.props["tax_total"]

    @property
    def grand_total(self) -> Money:
        return self.props["grand_total"]
