from decimal import Decimal
from typing import Any, Optional
from ..shared.entities.entity import Entity
from ..shared.entities.identifier import UniqueEntityID
from .value_objects import Money


class LineItemId(UniqueEntityID):
    """Identifier representing a Line Item's unique identity."""
    pass


class InvoiceLineItem(Entity[LineItemId]):
    """
    Domain Entity representing an individual line item on an invoice.
    Managed within the Invoice aggregate boundary.
    """
    def __init__(
        self,
        id: LineItemId,
        description: str,
        quantity: Decimal,
        unit_price: Money,
        discount: Optional[Money] = None,
        tax: Optional[Money] = None
    ):
        super().__init__(id)
        self._validate_inputs(description, quantity, unit_price, discount, tax)

        self._description = description.strip()
        self._quantity = Decimal(str(quantity))
        self._unit_price = unit_price

        # Default discount and tax to zero in the same currency as unit_price
        currency = unit_price.currency
        self._discount = discount or Money(Decimal("0.00"), currency)
        self._tax = tax or Money(Decimal("0.00"), currency)

        self._recalculate_amount()

    # Getters
    @property
    def description(self) -> str:
        return self._description

    @property
    def quantity(self) -> Decimal:
        return self._quantity

    @property
    def unit_price(self) -> Money:
        return self._unit_price

    @property
    def discount(self) -> Money:
        return self._discount

    @property
    def tax(self) -> Money:
        return self._tax

    @property
    def amount(self) -> Money:
        """The total net amount of the line item (quantity * unit_price - discount)."""
        return self._amount

    # Update methods (internal to aggregate boundary)
    def update(
        self,
        description: str,
        quantity: Decimal,
        unit_price: Money,
        discount: Optional[Money] = None,
        tax: Optional[Money] = None
    ) -> None:
        """Updates the line item details and recalculates the amount."""
        self._validate_inputs(description, quantity, unit_price, discount, tax)

        self._description = description.strip()
        self._quantity = Decimal(str(quantity))
        self._unit_price = unit_price

        currency = unit_price.currency
        self._discount = discount or Money(Decimal("0.00"), currency)
        self._tax = tax or Money(Decimal("0.00"), currency)

        self._recalculate_amount()

    def _recalculate_amount(self) -> None:
        """Calculates quantity * unit_price - discount."""
        base_total = self._unit_price * self._quantity
        self._amount = base_total - self._discount

    def _validate_inputs(
        self,
        description: str,
        quantity: Decimal,
        unit_price: Money,
        discount: Optional[Money] = None,
        tax: Optional[Money] = None
    ) -> None:
        """Validates consistency and correctness of line item attributes."""
        if not description or not isinstance(description, str) or description.strip() == "":
            raise ValueError("Line item description cannot be empty.")

        if quantity is None:
            raise ValueError("Line item quantity cannot be null.")
        if isinstance(quantity, float):
            raise TypeError("Floating point numbers are not allowed for line item quantity. Use Decimal.")
        dec_qty = Decimal(str(quantity))
        if dec_qty <= 0:
            raise ValueError("Line item quantity must be greater than zero.")

        if not isinstance(unit_price, Money):
            raise ValueError("Line item unit price must be a Money object.")
        if unit_price.amount <= 0:
            raise ValueError("Line item unit price must be greater than zero.")

        currency = unit_price.currency

        if discount:
            if not isinstance(discount, Money):
                raise ValueError("Line item discount must be a Money object.")
            if discount.currency != currency:
                raise ValueError("Line item discount currency must match unit price currency.")
            if discount.amount < 0:
                raise ValueError("Line item discount cannot be negative.")
            # Discount cannot exceed gross amount
            gross_amount = unit_price.amount * dec_qty
            if discount.amount > gross_amount:
                raise ValueError("Line item discount cannot exceed the gross item amount.")

        if tax:
            if not isinstance(tax, Money):
                raise ValueError("Line item tax must be a Money object.")
            if tax.currency != currency:
                raise ValueError("Line item tax currency must match unit price currency.")
            if tax.amount < 0:
                raise ValueError("Line item tax cannot be negative.")
