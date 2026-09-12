from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId

from .value_objects import (
    InvoiceId,
    CustomerId,
    InvoiceNumber,
    Currency,
    Money,
    BillingSnapshot,
    InvoiceOrigin,
    TaxLine,
    Adjustment,
    PaymentTerms,
    InvoiceTotals
)
from .line_item import InvoiceLineItem, LineItemId
from .events import InvoiceCreated, InvoiceIssued, InvoiceVoided
from .services import InvoiceCalculationService
from .specifications import InvoiceCanBeIssued, InvoiceCanBeVoided


class InvoiceStatus(str, Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PAID = "PAID"
    VOIDED = "VOIDED"


class Invoice(AggregateRoot[InvoiceId]):
    """
    Invoice Aggregate Root.
    Guards commercial facts and lifecycle transitions (DRAFT -> ISSUED -> VOIDED).
    Enforces strict immutability of the commercial document upon issuance.
    """
    def __init__(self, id: InvoiceId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    # Getters
    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def invoice_number(self) -> Optional[InvoiceNumber]:
        return self._props.get("invoice_number")

    @property
    def origin(self) -> InvoiceOrigin:
        return self._props["origin"]

    @property
    def customer_id(self) -> Optional[CustomerId]:
        return self._props.get("customer_id")

    @property
    def billing_snapshot(self) -> Optional[BillingSnapshot]:
        return self._props.get("billing_snapshot")

    @property
    def currency(self) -> Currency:
        return self._props["currency"]

    @property
    def line_items(self) -> Tuple[InvoiceLineItem, ...]:
        return tuple(self._props["line_items"])

    @property
    def adjustments(self) -> Tuple[Adjustment, ...]:
        return tuple(self._props["adjustments"])

    @property
    def taxes(self) -> Tuple[TaxLine, ...]:
        return tuple(self._props["taxes"])

    @property
    def totals(self) -> InvoiceTotals:
        return self._props["totals"]

    @property
    def payment_terms(self) -> PaymentTerms:
        return self._props["payment_terms"]

    @property
    def lifecycle_status(self) -> InvoiceStatus:
        return self._props["lifecycle_status"]

    @property
    def issued_at(self) -> Optional[datetime]:
        return self._props.get("issued_at")

    @property
    def due_at(self) -> Optional[datetime]:
        return self._props.get("due_at")

    @property
    def cancelled_at(self) -> Optional[datetime]:
        return self._props.get("cancelled_at")

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: InvoiceId,
        organization_id: OrganizationId,
        origin: InvoiceOrigin,
        currency: Currency,
        payment_terms: PaymentTerms,
        customer_id: Optional[CustomerId] = None,
        billing_snapshot: Optional[BillingSnapshot] = None
    ) -> Result["Invoice"]:
        """
        Factory method to create a new Invoice in DRAFT status.
        Registers the InvoiceCreated domain event.
        """
        if None in (id, organization_id, origin, currency, payment_terms):
            return Result.fail(ResultError.validation("Mandatory fields cannot be null."))

        now = Clock.now()
        zero_money = Money(Decimal("0.00"), currency)

        initial_totals = InvoiceTotals(
            subtotal=zero_money,
            discount_total=zero_money,
            charge_total=zero_money,
            tax_total=zero_money,
            grand_total=zero_money
        )

        props = {
            "organization_id": organization_id,
            "invoice_number": None,
            "origin": origin,
            "customer_id": customer_id,
            "billing_snapshot": billing_snapshot,
            "currency": currency,
            "line_items": [],
            "adjustments": [],
            "taxes": [],
            "totals": initial_totals,
            "payment_terms": payment_terms,
            "lifecycle_status": InvoiceStatus.DRAFT,
            "issued_at": None,
            "due_at": None,
            "cancelled_at": None,
            "created_at": now,
            "updated_at": now
        }

        invoice = cls(id, props)
        invoice._add_domain_event(InvoiceCreated(id, organization_id, now))
        return Result.ok(invoice)

    def _ensure_draft(self) -> Result[None]:
        """Utility to block updates to issued or voided invoices."""
        if self.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(
                ResultError.conflict(
                    f"Invoice cannot be modified because its current status is {self.lifecycle_status}."
                )
            )
        return Result.ok()

    def _recalculate_totals(self) -> None:
        """Recalculates invoice totals using the InvoiceCalculationService."""
        new_totals = InvoiceCalculationService.calculate_totals(
            line_items=self._props["line_items"],
            adjustments=self._props["adjustments"],
            tax_lines=self._props["taxes"],
            currency=self.currency
        )
        self._props["totals"] = new_totals

    def update_customer(
        self,
        customer_id: Optional[CustomerId],
        billing_snapshot: Optional[BillingSnapshot]
    ) -> Result[None]:
        """Updates the buyer customer reference and billing snapshot details."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        self._props["customer_id"] = customer_id
        self._props["billing_snapshot"] = billing_snapshot
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def add_line_item(self, line_item: InvoiceLineItem) -> Result[None]:
        """Appends a new line item to the invoice."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        if line_item is None:
            return Result.fail(ResultError.validation("Line item cannot be null."))

        if line_item.unit_price.currency != self.currency:
            return Result.fail(
                ResultError.validation("Line item currency does not match invoice currency.")
            )

        # Check for duplicate item ID
        for existing in self._props["line_items"]:
            if existing.id == line_item.id:
                return Result.fail(ResultError.conflict("Line item with this ID already exists on the invoice."))

        self._props["line_items"].append(line_item)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def update_line_item(
        self,
        line_item_id: LineItemId,
        description: str,
        quantity: Decimal,
        unit_price: Money,
        discount: Optional[Money] = None,
        tax: Optional[Money] = None
    ) -> Result[None]:
        """Updates attributes of an existing line item."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        # Find target item
        target_item = None
        for item in self._props["line_items"]:
            if item.id == line_item_id:
                target_item = item
                break

        if not target_item:
            return Result.fail(ResultError.not_found("Line item not found on this invoice."))

        try:
            target_item.update(
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                discount=discount,
                tax=tax
            )
        except (ValueError, TypeError) as e:
            return Result.fail(ResultError.validation(str(e)))

        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def remove_line_item(self, line_item_id: LineItemId) -> Result[None]:
        """Removes a line item from the invoice."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        # Find item index
        target_index = -1
        for idx, item in enumerate(self._props["line_items"]):
            if item.id == line_item_id:
                target_index = idx
                break

        if target_index == -1:
            return Result.fail(ResultError.not_found("Line item not found on this invoice."))

        self._props["line_items"].pop(target_index)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def add_adjustment(self, adjustment: Adjustment) -> Result[None]:
        """Adds an invoice-level adjustment (discount or charge)."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        if adjustment is None:
            return Result.fail(ResultError.validation("Adjustment cannot be null."))

        if adjustment.amount.currency != self.currency:
            return Result.fail(
                ResultError.validation("Adjustment currency does not match invoice currency.")
            )

        self._props["adjustments"].append(adjustment)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def remove_adjustment(self, adjustment: Adjustment) -> Result[None]:
        """Removes an invoice-level adjustment based on structural equality."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        target_index = -1
        for idx, adj in enumerate(self._props["adjustments"]):
            if adj.equals(adjustment):
                target_index = idx
                break

        if target_index == -1:
            return Result.fail(ResultError.not_found("Adjustment not found on this invoice."))

        self._props["adjustments"].pop(target_index)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def add_tax_line(self, tax_line: TaxLine) -> Result[None]:
        """Adds an invoice-level tax line."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        if tax_line is None:
            return Result.fail(ResultError.validation("Tax line cannot be null."))

        if tax_line.amount.currency != self.currency:
            return Result.fail(
                ResultError.validation("Tax line currency does not match invoice currency.")
            )

        self._props["taxes"].append(tax_line)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def remove_tax_line(self, tax_line: TaxLine) -> Result[None]:
        """Removes an invoice-level tax line based on structural equality."""
        draft_check = self._ensure_draft()
        if draft_check.is_failure:
            return draft_check

        target_index = -1
        for idx, tax in enumerate(self._props["taxes"]):
            if tax.equals(tax_line):
                target_index = idx
                break

        if target_index == -1:
            return Result.fail(ResultError.not_found("Tax line not found on this invoice."))

        self._props["taxes"].pop(target_index)
        self._recalculate_totals()
        self._props["updated_at"] = Clock.now()
        return Result.ok()

    def issue(self, invoice_number: InvoiceNumber, issued_at: Optional[datetime] = None) -> Result[None]:
        """
        Transitions the Invoice from DRAFT to ISSUED.
        Locks commercial details, sets invoice number, and derives the due date.
        """
        can_issue = InvoiceCanBeIssued().is_satisfied_by(self)
        if not can_issue:
            return Result.fail(
                ResultError.validation(
                    "Invoice does not satisfy requirements to be issued. "
                    "Must be in DRAFT state, have at least one line item, and a billing snapshot."
                )
            )

        if invoice_number is None:
            return Result.fail(ResultError.validation("Invoice number cannot be null."))

        now = issued_at or Clock.now()

        self._props["invoice_number"] = invoice_number
        self._props["lifecycle_status"] = InvoiceStatus.ISSUED
        self._props["issued_at"] = now
        # Derive due date using the payment terms
        self._props["due_at"] = self.payment_terms.derive_due_date(now)

        self._props["updated_at"] = now

        self._add_domain_event(InvoiceIssued(self.id, invoice_number, self.organization_id, now))
        return Result.ok()

    def void(self, voided_at: Optional[datetime] = None) -> Result[None]:
        """
        Transitions the Invoice to VOIDED state.
        Permitted from DRAFT or ISSUED state.
        """
        can_void = InvoiceCanBeVoided().is_satisfied_by(self)
        if not can_void:
            return Result.fail(ResultError.conflict("Invoice is already voided."))

        now = voided_at or Clock.now()

        self._props["lifecycle_status"] = InvoiceStatus.VOIDED
        self._props["cancelled_at"] = now
        self._props["updated_at"] = now

        self._add_domain_event(InvoiceVoided(self.id, self.organization_id, now))
        return Result.ok()

    def mark_as_paid(self, paid_at: Optional[datetime] = None) -> Result[None]:
        """
        Transitions the Invoice to PAID state when full payment allocation is reconciled.
        """
        now = paid_at or Clock.now()
        self._props["lifecycle_status"] = InvoiceStatus.PAID
        self._props["updated_at"] = now
        return Result.ok()
