import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from nomadx.src.domain.shared.clock.clock import Clock, ControllableClock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice import (
    Invoice,
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
    InvoiceStatus,
    InvoiceLineItem,
    LineItemId,
    InvoiceCreated,
    InvoiceIssued,
    InvoiceVoided
)


@pytest.fixture(autouse=True)
def setup_clock():
    # Setup test controllable clock
    initial_time = datetime(2026, 8, 15, 12, 0, 0)
    clock = ControllableClock(initial_time)
    Clock.set_provider(clock)
    yield clock
    Clock.reset()


def test_invoice_creation_success():
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    cust_id = CustomerId.generate()
    buyer = BillingSnapshot(
        legal_name="Acme Corp",
        email="billing@acme.com",
        address="123 Road",
        country="USA"
    )

    result = Invoice.create(
        id=inv_id,
        organization_id=org_id,
        origin=origin,
        currency=usd,
        payment_terms=terms,
        customer_id=cust_id,
        billing_snapshot=buyer
    )

    assert result.is_success
    invoice = result.value
    assert invoice.id == inv_id
    assert invoice.organization_id == org_id
    assert invoice.lifecycle_status == InvoiceStatus.DRAFT
    assert len(invoice.line_items) == 0
    assert len(invoice.adjustments) == 0
    assert len(invoice.taxes) == 0
    assert invoice.totals.grand_total.amount == Decimal("0.00")
    
    # Verify domain event
    assert len(invoice.domain_events) == 1
    assert isinstance(invoice.domain_events[0], InvoiceCreated)
    assert invoice.domain_events[0].get_aggregate_id() == str(inv_id.value)


def test_add_and_update_line_item_success():
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    
    invoice = Invoice.create(inv_id, org_id, origin, usd, terms).value

    # Create Line Item
    item_id = LineItemId.generate()
    item = InvoiceLineItem(
        id=item_id,
        description="Consulting Hours",
        quantity=Decimal("10.5"),
        unit_price=Money("100.00", usd),
        discount=Money("50.00", usd),
        tax=Money("25.00", usd)
    )

    # Add item
    add_res = invoice.add_line_item(item)
    assert add_res.is_success
    assert len(invoice.line_items) == 1
    
    # Net amount calculation: 10.5 * 100 - 50 = 1000.00 (tax is exclusive)
    assert invoice.line_items[0].amount.amount == Decimal("1000.00")
    assert invoice.totals.subtotal.amount == Decimal("1000.00")
    assert invoice.totals.discount_total.amount == Decimal("0.00")
    assert invoice.totals.tax_total.amount == Decimal("25.00")
    assert invoice.totals.grand_total.amount == Decimal("1025.00")

    # Update item
    upd_res = invoice.update_line_item(
        line_item_id=item_id,
        description="Senior Consulting Hours",
        quantity=Decimal("5.0"),
        unit_price=Money("200.00", usd),
        discount=Money("10.00", usd),
        tax=Money("50.00", usd)
    )
    assert upd_res.is_success
    
    # Net amount calculation: 5.0 * 200 - 10 = 990.00 (tax is exclusive)
    assert invoice.line_items[0].description == "Senior Consulting Hours"
    assert invoice.line_items[0].amount.amount == Decimal("990.00")
    assert invoice.totals.subtotal.amount == Decimal("990.00")
    assert invoice.totals.discount_total.amount == Decimal("0.00")
    assert invoice.totals.tax_total.amount == Decimal("50.00")
    assert invoice.totals.grand_total.amount == Decimal("1040.00")

    # Remove item
    rem_res = invoice.remove_line_item(item_id)
    assert rem_res.is_success
    assert len(invoice.line_items) == 0
    assert invoice.totals.grand_total.amount == Decimal("0.00")


def test_line_item_add_validations():
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    eur = Currency("EUR", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    
    invoice = Invoice.create(inv_id, org_id, origin, usd, terms).value

    # Currency mismatch
    item_eur = InvoiceLineItem(
        id=LineItemId.generate(),
        description="Consulting",
        quantity=Decimal("1"),
        unit_price=Money("100.00", eur)
    )
    res = invoice.add_line_item(item_eur)
    assert res.is_failure
    assert "currency" in res.error.message.lower()


def test_invoice_totals_formula_and_adjustments():
    # Verify the specific receipt totals from the requirements:
    # Subtotal 224.00, Tax 11.20, Grand Total 235.20
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    inr = Currency("INR", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    
    invoice = Invoice.create(inv_id, org_id, origin, inr, terms).value

    # Line Item 1: Veg Parcel, Qty 1, unit price 55
    item1 = InvoiceLineItem(
        id=LineItemId.generate(),
        description="Veg Parcel",
        quantity=Decimal("1"),
        unit_price=Money("55.00", inr)
    )
    invoice.add_line_item(item1)

    # Line Item 2: Taco Mexicana Veg, Qty 1, unit price 169
    item2 = InvoiceLineItem(
        id=LineItemId.generate(),
        description="Taco Mexicana Veg",
        quantity=Decimal("1"),
        unit_price=Money("169.00", inr)
    )
    invoice.add_line_item(item2)

    # Add tax lines: CGST @ 2.5%, SGST @ 2.5%
    # CGST = 5.60, SGST = 5.60
    cgst = TaxLine(
        code="CGST",
        name="Central GST",
        rate=Decimal("0.025"),
        taxable_amount=Money("224.00", inr),
        amount=Money("5.60", inr)
    )
    sgst = TaxLine(
        code="SGST",
        name="State GST",
        rate=Decimal("0.025"),
        taxable_amount=Money("224.00", inr),
        amount=Money("5.60", inr)
    )
    
    invoice.add_tax_line(cgst)
    invoice.add_tax_line(sgst)

    # Verify Totals
    assert invoice.totals.subtotal.amount == Decimal("224.00")
    assert invoice.totals.discount_total.amount == Decimal("0.00")
    assert invoice.totals.charge_total.amount == Decimal("0.00")
    assert invoice.totals.tax_total.amount == Decimal("11.20")
    assert invoice.totals.grand_total.amount == Decimal("235.20")

    # Add a discount adjustment of 10.00 INR and charge adjustment of 5.00 INR
    discount_adj = Adjustment(
        type=AdjustmentType.DISCOUNT,
        description="Loyalty Discount",
        amount=Money("10.00", inr)
    )
    charge_adj = Adjustment(
        type=AdjustmentType.CHARGE,
        description="Delivery Fee",
        amount=Money("5.00", inr)
    )

    invoice.add_adjustment(discount_adj)
    invoice.add_adjustment(charge_adj)

    # Grand total: 224.00 - 10.00 + 5.00 + 11.20 = 230.20
    assert invoice.totals.discount_total.amount == Decimal("10.00")
    assert invoice.totals.charge_total.amount == Decimal("5.00")
    assert invoice.totals.grand_total.amount == Decimal("230.20")

    # Remove discount adjustment
    invoice.remove_adjustment(discount_adj)
    assert invoice.totals.discount_total.amount == Decimal("0.00")
    assert invoice.totals.grand_total.amount == Decimal("240.20")


def test_invoice_lifecycle_transitions(setup_clock):
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    cust_id = CustomerId.generate()
    buyer = BillingSnapshot(
        legal_name="Acme Corp",
        email="billing@acme.com",
        address="123 Road",
        country="USA"
    )

    invoice = Invoice.create(inv_id, org_id, origin, usd, terms, cust_id, buyer).value

    # Add a line item so we can issue it
    item = InvoiceLineItem(
        id=LineItemId.generate(),
        description="Consulting",
        quantity=Decimal("5"),
        unit_price=Money("100.00", usd)
    )
    invoice.add_line_item(item)

    # Verify we can issue
    invoice_number = InvoiceNumber("INV-2026-000123")
    
    issue_time = datetime(2026, 8, 15, 12, 10, 0)
    setup_clock.set_time(issue_time)

    # Issue
    issue_res = invoice.issue(invoice_number)
    assert issue_res.is_success
    assert invoice.lifecycle_status == InvoiceStatus.ISSUED
    assert invoice.invoice_number == invoice_number
    assert invoice.issued_at == issue_time
    
    # Net 30 should set due date to issue_time + 30 days
    assert invoice.due_at == issue_time + timedelta(days=30)

    # Check that events are populated
    assert len(invoice.domain_events) == 2
    assert isinstance(invoice.domain_events[1], InvoiceIssued)

    # Verify IMMUTABILITY of ISSUED invoices
    # Attempting to add, update, remove items, adjustments, taxes must fail
    res_add = invoice.add_line_item(item)
    assert res_add.is_failure
    
    res_upd = invoice.update_line_item(item.id, "Attempt", Decimal("2"), Money("50.00", usd))
    assert res_upd.is_failure

    res_rem = invoice.remove_line_item(item.id)
    assert res_rem.is_failure

    res_cust = invoice.update_customer(cust_id, buyer)
    assert res_cust.is_failure

    # Void the ISSUED invoice
    void_time = datetime(2026, 8, 15, 12, 20, 0)
    setup_clock.set_time(void_time)
    
    void_res = invoice.void()
    assert void_res.is_success
    assert invoice.lifecycle_status == InvoiceStatus.VOIDED
    assert invoice.cancelled_at == void_time
    assert isinstance(invoice.domain_events[-1], InvoiceVoided)

    # Cannot void again
    void_again = invoice.void()
    assert void_again.is_failure


def test_void_draft_invoice():
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)

    invoice = Invoice.create(inv_id, org_id, origin, usd, terms).value
    
    # Void from DRAFT
    res = invoice.void()
    assert res.is_success
    assert invoice.lifecycle_status == InvoiceStatus.VOIDED
    assert isinstance(invoice.domain_events[-1], InvoiceVoided)


def test_cannot_issue_without_line_items_or_billing_snapshot():
    org_id = OrganizationId.generate()
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(InvoiceOriginType.NATIVE)
    terms = PaymentTerms(PaymentTermsType.NET_30)

    # No billing snapshot during creation
    invoice = Invoice.create(inv_id, org_id, origin, usd, terms).value

    # Attempt to issue with no line items and no buyer info
    invoice_number = InvoiceNumber("INV-2026-000123")
    res = invoice.issue(invoice_number)
    assert res.is_failure
    assert "satisfy requirements" in res.error.message.lower()

    # Add buyer info but no line items
    buyer = BillingSnapshot("Acme", "a@a.com", "Road", "USA")
    invoice.update_customer(CustomerId.generate(), buyer)
    
    res2 = invoice.issue(invoice_number)
    assert res2.is_failure
    
    # Add line item
    item = InvoiceLineItem(LineItemId.generate(), "Desc", Decimal("1"), Money("10.00", usd))
    invoice.add_line_item(item)
    
    # Should work now
    res3 = invoice.issue(invoice_number)
    assert res3.is_success
