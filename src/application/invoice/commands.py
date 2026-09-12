from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ...domain.shared.result.result import Result, ResultError
from ...domain.organization.value_objects import OrganizationId

from ...domain.invoice import (
    Invoice,
    InvoiceStatus,
    InvoiceLineItem,
    LineItemId,
    InvoiceId,
    CustomerId,
    InvoiceNumber,
    Currency,
    CurrencyType,
    Money,
    BillingSnapshot,
    InvoiceOrigin,
    InvoiceOriginType,
    TaxLine,
    Adjustment,
    AdjustmentType,
    PaymentTerms,
    PaymentTermsType,
    InvoiceTotals
)
from ...domain.invoice.repositories import IInvoiceRepository
from .errors import InvoiceErrors


@dataclass
class ExecutionContext:
    user_id: str
    organization_id: str
    role: str


# DTO Mapper Helper
def _to_dto(invoice: Invoice) -> Dict[str, Any]:
    return {
        "id": invoice.id.value,
        "organization_id": invoice.organization_id.value,
        "invoice_number": invoice.invoice_number.value if invoice.invoice_number else None,
        "status": invoice.lifecycle_status.value,
        "currency_code": invoice.currency.code,
        "origin": {
            "type": invoice.origin.type.value,
            "source_system": invoice.origin.source_system,
            "external_invoice_id": invoice.origin.external_invoice_id
        },
        "customer_id": invoice.customer_id.value if invoice.customer_id else None,
        "billing_snapshot": {
            "legal_name": invoice.billing_snapshot.legal_name,
            "email": invoice.billing_snapshot.email,
            "address": invoice.billing_snapshot.address,
            "country": invoice.billing_snapshot.country,
            "tax_identifier": invoice.billing_snapshot.tax_identifier
        } if invoice.billing_snapshot else None,
        "currency": {
            "code": invoice.currency.code,
            "type": invoice.currency.type.value
        },
        "lifecycle_status": invoice.lifecycle_status.value,
        "totals": {
            "subtotal": str(invoice.totals.subtotal.amount),
            "discount_total": str(invoice.totals.discount_total.amount),
            "charge_total": str(invoice.totals.charge_total.amount),
            "tax_total": str(invoice.totals.tax_total.amount),
            "grand_total": str(invoice.totals.grand_total.amount)
        },
        "payment_terms": {
            "terms_type": invoice.payment_terms.terms_type.value,
            "due_at": invoice.payment_terms.due_at.isoformat() if invoice.payment_terms.due_at else None
        },
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "due_at": invoice.due_at.isoformat() if invoice.due_at else None,
        "cancelled_at": invoice.cancelled_at.isoformat() if invoice.cancelled_at else None,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat(),
        "line_items": [
            {
                "id": item.id.value,
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price.amount),
                "discount": str(item.discount.amount),
                "tax": str(item.tax.amount),
                "amount": str(item.amount.amount)
            } for item in invoice.line_items
        ],
        "adjustments": [
            {
                "type": adj.type.value,
                "description": adj.description,
                "amount": str(adj.amount.amount),
                "rate": str(adj.rate) if adj.rate is not None else None
            } for adj in invoice.adjustments
        ],
        "taxes": [
            {
                "code": tax.code,
                "name": tax.name,
                "rate": str(tax.rate),
                "taxable_amount": str(tax.taxable_amount.amount),
                "amount": str(tax.amount.amount)
            } for tax in invoice.taxes
        ]
    }


# Use cases handlers

@dataclass
class CreateInvoiceCommand:
    invoice_id: str
    origin_type: str
    source_system: Optional[str]
    external_invoice_id: Optional[str]
    currency_code: str
    currency_type: str
    payment_terms_type: str
    due_at: Optional[datetime] = None
    customer_id: Optional[str] = None
    billing_snapshot: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None


class CreateInvoiceHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: CreateInvoiceCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        # Tenant boundary check
        if command.organization_id and command.organization_id != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        inv_id = InvoiceId(command.invoice_id)
        if await self._invoice_repo.exists(inv_id):
            return Result.fail(ResultError.conflict(f"Invoice with ID '{inv_id.value}' already exists."))

        try:
            org_id = OrganizationId(ctx.organization_id)
            origin = InvoiceOrigin(
                type=InvoiceOriginType(command.origin_type),
                source_system=command.source_system,
                external_invoice_id=command.external_invoice_id
            )
            currency = Currency(code=command.currency_code, type=CurrencyType(command.currency_type))
            terms = PaymentTerms(
                terms_type=PaymentTermsType(command.payment_terms_type),
                due_at=command.due_at
            )
            cust_id = CustomerId(command.customer_id) if command.customer_id else None
            
            billing_snapshot = None
            if command.billing_snapshot:
                billing_snapshot = BillingSnapshot(
                    legal_name=command.billing_snapshot.get("legal_name", ""),
                    email=command.billing_snapshot.get("email", ""),
                    address=command.billing_snapshot.get("address", ""),
                    country=command.billing_snapshot.get("country", ""),
                    tax_identifier=command.billing_snapshot.get("tax_identifier")
                )
        except ValueError as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        invoice_res = Invoice.create(
            id=inv_id,
            organization_id=org_id,
            origin=origin,
            currency=currency,
            payment_terms=terms,
            customer_id=cust_id,
            billing_snapshot=billing_snapshot
        )
        if invoice_res.is_failure:
            return Result.fail(invoice_res.error)

        invoice = invoice_res.value
        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class AddInvoiceLineItemCommand:
    invoice_id: str
    line_item_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None


class AddInvoiceLineItemHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: AddInvoiceLineItemCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        try:
            item_id = LineItemId(command.line_item_id)
            price = Money(command.unit_price, invoice.currency)
            discount = Money(command.discount, invoice.currency) if command.discount is not None else None
            tax = Money(command.tax, invoice.currency) if command.tax is not None else None

            item = InvoiceLineItem(
                id=item_id,
                description=command.description,
                quantity=command.quantity,
                unit_price=price,
                discount=discount,
                tax=tax
            )
        except (ValueError, TypeError) as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        res = invoice.add_line_item(item)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class UpdateInvoiceLineItemCommand:
    invoice_id: str
    line_item_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None


class UpdateInvoiceLineItemHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: UpdateInvoiceLineItemCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        try:
            item_id = LineItemId(command.line_item_id)
            price = Money(command.unit_price, invoice.currency)
            discount = Money(command.discount, invoice.currency) if command.discount is not None else None
            tax = Money(command.tax, invoice.currency) if command.tax is not None else None
        except (ValueError, TypeError) as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        res = invoice.update_line_item(
            line_item_id=item_id,
            description=command.description,
            quantity=command.quantity,
            unit_price=price,
            discount=discount,
            tax=tax
        )
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class RemoveInvoiceLineItemCommand:
    invoice_id: str
    line_item_id: str


class RemoveInvoiceLineItemHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: RemoveInvoiceLineItemCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        item_id = LineItemId(command.line_item_id)
        res = invoice.remove_line_item(item_id)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class AddInvoiceTaxLineCommand:
    invoice_id: str
    code: str
    name: str
    rate: Decimal
    taxable_amount: Decimal
    amount: Decimal


class AddInvoiceTaxLineHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: AddInvoiceTaxLineCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        try:
            taxable = Money(command.taxable_amount, invoice.currency)
            amount = Money(command.amount, invoice.currency)
            tax_line = TaxLine(
                code=command.code,
                name=command.name,
                rate=command.rate,
                taxable_amount=taxable,
                amount=amount
            )
        except (ValueError, TypeError) as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        res = invoice.add_tax_line(tax_line)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class RemoveInvoiceTaxLineCommand:
    invoice_id: str
    code: str
    name: Optional[str] = None
    rate: Optional[Decimal] = None
    taxable_amount: Optional[Decimal] = None
    amount: Optional[Decimal] = None


class RemoveInvoiceTaxLineHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: RemoveInvoiceTaxLineCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        # Find existing tax line by code
        target_tax = None
        for tax in invoice.taxes:
            if tax.code.upper() == command.code.upper():
                target_tax = tax
                break

        if not target_tax:
            return Result.fail(InvoiceErrors.not_found("Tax line not found on this invoice."))

        res = invoice.remove_tax_line(target_tax)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class AddInvoiceAdjustmentCommand:
    invoice_id: str
    type: str  # DISCOUNT or CHARGE
    description: str
    amount: Decimal
    rate: Optional[Decimal] = None


class AddInvoiceAdjustmentHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: AddInvoiceAdjustmentCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        try:
            amount = Money(command.amount, invoice.currency)
            adj = Adjustment(
                type=AdjustmentType(command.type),
                description=command.description,
                amount=amount,
                rate=command.rate
            )
        except (ValueError, TypeError) as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        res = invoice.add_adjustment(adj)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class RemoveInvoiceAdjustmentCommand:
    invoice_id: str
    description: str
    type: Optional[str] = None
    amount: Optional[Decimal] = None
    rate: Optional[Decimal] = None


class RemoveInvoiceAdjustmentHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: RemoveInvoiceAdjustmentCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Status check
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.not_editable())

        # Find existing adjustment by description
        target_adj = None
        for adj in invoice.adjustments:
            if adj.description.upper() == command.description.upper():
                target_adj = adj
                break

        if not target_adj:
            return Result.fail(InvoiceErrors.not_found("Adjustment not found on this invoice."))

        res = invoice.remove_adjustment(target_adj)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class IssueInvoiceCommand:
    invoice_id: str
    invoice_number: str
    issued_at: Optional[datetime] = None


class IssueInvoiceHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: IssueInvoiceCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Check editable status
        if invoice.lifecycle_status != InvoiceStatus.DRAFT:
            return Result.fail(InvoiceErrors.invalid_state("Invoice is not in DRAFT state and cannot be issued."))

        try:
            inv_num = InvoiceNumber(command.invoice_number)
        except ValueError as ex:
            return Result.fail(InvoiceErrors.invalid_data(str(ex)))

        # Unique Invoice Number constraint within the Organization
        existing = await self._invoice_repo.find_by_invoice_number(
            invoice.organization_id,
            inv_num
        )
        if existing and existing.id.value != invoice.id.value:
            return Result.fail(InvoiceErrors.duplicate_number())

        # Delegate to domain aggregate root
        res = invoice.issue(inv_num, command.issued_at)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))


@dataclass
class VoidInvoiceCommand:
    invoice_id: str
    voided_at: Optional[datetime] = None


class VoidInvoiceHandler:
    def __init__(self, invoice_repo: IInvoiceRepository):
        self._invoice_repo = invoice_repo

    async def handle(self, command: VoidInvoiceCommand, ctx: ExecutionContext) -> Result[Dict[str, Any]]:
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(InvoiceErrors.not_found())

        # Tenant boundary check
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(InvoiceErrors.unauthorized())

        # Delegate to domain aggregate root
        res = invoice.void(command.voided_at)
        if res.is_failure:
            return Result.fail(res.error)

        await self._invoice_repo.save(invoice)
        return Result.ok(_to_dto(invoice))
