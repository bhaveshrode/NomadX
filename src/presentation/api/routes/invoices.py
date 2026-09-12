from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Depends, Path, Query
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.invoice import InvoiceId, LineItemId
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresInvoiceRepository

from ....application.invoice.commands import (
    CreateInvoiceCommand,
    AddInvoiceLineItemCommand,
    UpdateInvoiceLineItemCommand,
    RemoveInvoiceLineItemCommand,
    AddInvoiceTaxLineCommand,
    RemoveInvoiceTaxLineCommand,
    AddInvoiceAdjustmentCommand,
    RemoveInvoiceAdjustmentCommand,
    IssueInvoiceCommand,
    VoidInvoiceCommand,
    _to_dto
)
from ....domain.shared.result.result import Result


router = APIRouter(prefix="/api/invoices", tags=["invoices"])


# HTTP Request Pydantic Schemas

class BillingSnapshotSchema(BaseModel):
    legal_name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    tax_identifier: Optional[str] = None


class CreateInvoiceRequest(BaseModel):
    invoice_id: Optional[str] = None
    origin_type: str = Field(default="NATIVE")
    source_system: Optional[str] = None
    external_invoice_id: Optional[str] = None
    currency_code: str = Field(...)
    currency_type: str = Field(default="FIAT")
    payment_terms_type: str = Field(...)
    due_at: Optional[datetime] = None
    customer_id: Optional[str] = None
    billing_snapshot: Optional[BillingSnapshotSchema] = None


class AddLineItemRequest(BaseModel):
    line_item_id: Optional[str] = None
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(...)
    unit_price: Decimal = Field(...)
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None


class UpdateLineItemRequest(BaseModel):
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(...)
    unit_price: Decimal = Field(...)
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None


class AddTaxLineRequest(BaseModel):
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    rate: Decimal = Field(...)
    taxable_amount: Decimal = Field(...)
    amount: Decimal = Field(...)


class AddAdjustmentRequest(BaseModel):
    type: str = Field(...) # DISCOUNT or CHARGE
    description: str = Field(..., min_length=1)
    amount: Decimal = Field(...)
    rate: Optional[Decimal] = None


class IssueInvoiceRequest(BaseModel):
    invoice_number: str = Field(..., min_length=1)
    issued_at: Optional[datetime] = None


class VoidInvoiceRequest(BaseModel):
    voided_at: Optional[datetime] = None


# Helper to map result errors to HTTP status codes
def _handle_result(result: Result, success_status: int = status.HTTP_200_OK):
    if result.is_failure:
        err = result.error
        if err.code in ("NOT_FOUND", "INVOICE_NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code == "UNAUTHORIZED_ACCESS":
            # Guard: do not leak existence of other tenant invoices, return 404
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
        elif err.code in ("INVOICE_NOT_EDITABLE", "INVALID_INVOICE_STATE", "DUPLICATE_INVOICE_NUMBER"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code in ("VALIDATION_ERROR", "INVALID_INVOICE_DATA"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)
    return result.value


# Routes

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: CreateInvoiceRequest,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Creates a new draft invoice under the caller's organization context, enforcing plan quotas.
    """
    from .billing import _get_org_sub, PLANS_CATALOG
    sub = _get_org_sub(ctx.organization_id)
    plan = next((p for p in PLANS_CATALOG if p.id == sub["tier"]), None)
    max_invoices = plan.invoices_allowance_monthly if plan else 100

    async with session_manager.session() as session:
        repo = PostgresInvoiceRepository(session)
        existing_invoices = await repo.find_by_organization(OrganizationId(ctx.organization_id))
        if len(existing_invoices) >= max_invoices:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Organization has reached its monthly invoice allowance ({len(existing_invoices)}/{max_invoices} invoices). Upgrade your subscription plan to create more invoices."
            )

    inv_id = payload.invoice_id or str(uuid4())
    command = CreateInvoiceCommand(
        invoice_id=inv_id,
        origin_type=payload.origin_type,
        source_system=payload.source_system,
        external_invoice_id=payload.external_invoice_id,
        currency_code=payload.currency_code,
        currency_type=payload.currency_type,
        payment_terms_type=payload.payment_terms_type,
        due_at=payload.due_at,
        customer_id=payload.customer_id,
        billing_snapshot=payload.billing_snapshot.model_dump() if payload.billing_snapshot else None,
        organization_id=ctx.organization_id
    )

    async with container.create_invoice_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result, status.HTTP_201_CREATED)


@router.get("", status_code=status.HTTP_200_OK)
async def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    invoice_number: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves all invoices belonging strictly to the caller's organization.
    """
    async with session_manager.session() as session:
        repo = PostgresInvoiceRepository(session)
        invoices = await repo.find_by_organization(OrganizationId(ctx.organization_id))

    dtos = []
    for inv in invoices:
        if status_filter and inv.lifecycle_status.value != status_filter:
            continue
        if invoice_number and (not inv.invoice_number or inv.invoice_number.value != invoice_number):
            continue
        if customer_id and (not inv.customer_id or inv.customer_id.value != customer_id):
            continue
        if from_date and inv.created_at < from_date:
            continue
        if to_date and inv.created_at > to_date:
            continue
        dtos.append(_to_dto(inv))

    return dtos[offset : offset + limit]


@router.get("/{invoice_id}", status_code=status.HTTP_200_OK)
async def get_invoice(
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves a specific invoice details by ID, enforcing tenant isolation.
    """
    async with session_manager.session() as session:
        repo = PostgresInvoiceRepository(session)
        invoice = await repo.find_by_id(InvoiceId(invoice_id))
        
    if not invoice or invoice.organization_id.value != ctx.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")

    return _to_dto(invoice)


@router.post("/{invoice_id}/line-items", status_code=status.HTTP_200_OK)
async def add_line_item(
    payload: AddLineItemRequest,
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    item_id = payload.line_item_id or str(uuid4())
    command = AddInvoiceLineItemCommand(
        invoice_id=invoice_id,
        line_item_id=item_id,
        description=payload.description,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        discount=payload.discount,
        tax=payload.tax
    )

    async with container.add_invoice_line_item_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.patch("/{invoice_id}/line-items/{line_item_id}", status_code=status.HTTP_200_OK)
async def update_line_item(
    payload: UpdateLineItemRequest,
    invoice_id: str = Path(...),
    line_item_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = UpdateInvoiceLineItemCommand(
        invoice_id=invoice_id,
        line_item_id=line_item_id,
        description=payload.description,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        discount=payload.discount,
        tax=payload.tax
    )

    async with container.update_invoice_line_item_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.delete("/{invoice_id}/line-items/{line_item_id}", status_code=status.HTTP_200_OK)
async def remove_line_item(
    invoice_id: str = Path(...),
    line_item_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = RemoveInvoiceLineItemCommand(
        invoice_id=invoice_id,
        line_item_id=line_item_id
    )

    async with container.remove_invoice_line_item_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.post("/{invoice_id}/taxes", status_code=status.HTTP_200_OK)
async def add_tax(
    payload: AddTaxLineRequest,
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = AddInvoiceTaxLineCommand(
        invoice_id=invoice_id,
        code=payload.code,
        name=payload.name,
        rate=payload.rate,
        taxable_amount=payload.taxable_amount,
        amount=payload.amount
    )

    async with container.add_invoice_tax_line_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.delete("/{invoice_id}/taxes/{tax_id}", status_code=status.HTTP_200_OK)
async def remove_tax(
    invoice_id: str = Path(...),
    tax_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = RemoveInvoiceTaxLineCommand(
        invoice_id=invoice_id,
        code=tax_id
    )

    async with container.remove_invoice_tax_line_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.post("/{invoice_id}/adjustments", status_code=status.HTTP_200_OK)
async def add_adjustment(
    payload: AddAdjustmentRequest,
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = AddInvoiceAdjustmentCommand(
        invoice_id=invoice_id,
        type=payload.type,
        description=payload.description,
        amount=payload.amount,
        rate=payload.rate
    )

    async with container.add_invoice_adjustment_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.delete("/{invoice_id}/adjustments/{adjustment_id}", status_code=status.HTTP_200_OK)
async def remove_adjustment(
    invoice_id: str = Path(...),
    adjustment_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = RemoveInvoiceAdjustmentCommand(
        invoice_id=invoice_id,
        description=adjustment_id
    )

    async with container.remove_invoice_adjustment_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.post("/{invoice_id}/issue", status_code=status.HTTP_200_OK)
async def issue_invoice(
    payload: IssueInvoiceRequest,
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = IssueInvoiceCommand(
        invoice_id=invoice_id,
        invoice_number=payload.invoice_number,
        issued_at=payload.issued_at
    )

    async with container.issue_invoice_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


@router.post("/{invoice_id}/void", status_code=status.HTTP_200_OK)
async def void_invoice(
    payload: VoidInvoiceRequest,
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    command = VoidInvoiceCommand(
        invoice_id=invoice_id,
        voided_at=payload.voided_at
    )

    async with container.void_invoice_handler() as handler:
        result = await handler.handle(command, ctx)
        
    return _handle_result(result)


from ....infrastructure.database.repositories import PostgresPaymentAllocationRepository

@router.get("/{invoice_id}/payments", status_code=status.HTTP_200_OK)
async def get_invoice_payments(
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    async with session_manager.session() as session:
        invoice_repo = PostgresInvoiceRepository(session)
        invoice = await invoice_repo.find_by_id(InvoiceId(invoice_id))
        if not invoice or invoice.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=404, detail="Invoice not found.")

        allocation_repo = PostgresPaymentAllocationRepository(session)
        allocations = await allocation_repo.find_by_invoice_id(invoice.id)
        
        return [
            {
                "allocation_id": alloc.id.value,
                "payment_id": alloc.payment_id.value,
                "allocated_amount": str(alloc.allocated_amount.amount),
                "currency": alloc.allocated_amount.currency.code,
                "status": alloc.status,
                "allocated_at": alloc.created_at.isoformat()
            }
            for alloc in allocations
        ]


@router.get("/{invoice_id}/balance", status_code=status.HTTP_200_OK)
async def get_invoice_balance(
    invoice_id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    async with session_manager.session() as session:
        invoice_repo = PostgresInvoiceRepository(session)
        invoice = await invoice_repo.find_by_id(InvoiceId(invoice_id))
        if not invoice or invoice.organization_id.value != ctx.organization_id:
            raise HTTPException(status_code=404, detail="Invoice not found.")

        allocation_repo = PostgresPaymentAllocationRepository(session)
        allocations = await allocation_repo.find_by_invoice_id(invoice.id)
        total_allocated = sum(alloc.allocated_amount.amount for alloc in allocations if alloc.status == "ALLOCATED")
        grand_total = invoice.totals.grand_total.amount
        outstanding = max(Decimal("0"), grand_total - total_allocated)

        return {
            "invoice_id": invoice.id.value,
            "total_amount": f"{grand_total:.2f}",
            "allocated_amount": f"{total_allocated:.2f}",
            "outstanding_amount": f"{outstanding:.2f}",
            "currency": invoice.currency.code
        }
