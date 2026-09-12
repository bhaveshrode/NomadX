import json
import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Domain imports
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
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
)

# Infrastructure imports
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel


@pytest.mark.asyncio
async def test_invoice_persistence_flow(db_session: AsyncSession):
    # 1. Setup prerequisite Organization in database
    org_repo = PostgresOrganizationRepository(db_session)
    org_id = OrganizationId.generate()
    org = Organization.create(
        id=org_id,
        name=OrganizationName("Test Merchant Org"),
        slug=OrganizationSlug("test-merchant-org"),
        owner_id=UserId.generate(),
        settings=OrganizationSettings("USD")
    ).value
    await org_repo.save(org)
    await db_session.commit()

    # 2. Create Invoice repo
    invoice_repo = PostgresInvoiceRepository(db_session)

    # 3. Create Draft Invoice
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    origin = InvoiceOrigin(
        type=InvoiceOriginType.EXTERNAL,
        source_system="QuickBooks",
        external_invoice_id="QB-998877"
    )
    terms = PaymentTerms(PaymentTermsType.NET_15)
    cust_id = CustomerId.generate()
    buyer = BillingSnapshot(
        legal_name="Corp Inc",
        email="billing@corp.com",
        address="Suite 100",
        country="US",
        tax_identifier="TAX-12345"
    )

    invoice = Invoice.create(
        id=inv_id,
        organization_id=org_id,
        origin=origin,
        currency=usd,
        payment_terms=terms,
        customer_id=cust_id,
        billing_snapshot=buyer
    ).value

    # 4. Add items, adjustments, and tax lines
    item_id = LineItemId.generate()
    item = InvoiceLineItem(
        id=item_id,
        description="Development Services",
        quantity=Decimal("15.5"),
        unit_price=Money("100.00", usd),
        discount=Money("50.00", usd),
        tax=Money("10.00", usd)
    )
    invoice.add_line_item(item)

    adj_discount = Adjustment(
        type=AdjustmentType.DISCOUNT,
        description="Promo Discount",
        amount=Money("15.00", usd)
    )
    invoice.add_adjustment(adj_discount)

    tax_line = TaxLine(
        code="VAT-10",
        name="Value Added Tax 10%",
        rate=Decimal("0.10"),
        taxable_amount=Money("1500.00", usd),
        amount=Money("150.00", usd)
    )
    invoice.add_tax_line(tax_line)

    # Verify initial totals before persisting
    # item.amount = 15.5 * 100 - 50 = 1500.00
    # subtotal = 1500.00
    # discount_total = 15.00
    # charge_total = 0.00
    # tax_total = 10.00 (from item) + 150.00 (from tax line) = 160.00
    # grand_total = 1500.00 - 15.00 + 0.00 + 160.00 = 1645.00
    assert invoice.totals.grand_total.amount == Decimal("1645.00")

    # 5. Save to database
    await invoice_repo.save(invoice)
    await db_session.commit()

    # 6. Retrieve and Reconstruct aggregate
    retrieved = await invoice_repo.find_by_id(inv_id)
    assert retrieved is not None
    assert retrieved.id == inv_id
    assert retrieved.organization_id == org_id
    assert retrieved.lifecycle_status == InvoiceStatus.DRAFT
    
    # Check origin
    assert retrieved.origin.type == InvoiceOriginType.EXTERNAL
    assert retrieved.origin.source_system == "QuickBooks"
    assert retrieved.origin.external_invoice_id == "QB-998877"

    # Check buyer billing snapshot
    assert retrieved.billing_snapshot is not None
    assert retrieved.billing_snapshot.legal_name == "Corp Inc"
    assert retrieved.billing_snapshot.email == "billing@corp.com"
    assert retrieved.billing_snapshot.tax_identifier == "TAX-12345"

    # Check payment terms
    assert retrieved.payment_terms.terms_type == PaymentTermsType.NET_15
    
    # Check totals
    assert retrieved.totals.subtotal.amount == Decimal("1500.00")
    assert retrieved.totals.discount_total.amount == Decimal("15.00")
    assert retrieved.totals.tax_total.amount == Decimal("160.00")
    assert retrieved.totals.grand_total.amount == Decimal("1645.00")

    # Check line items
    assert len(retrieved.line_items) == 1
    ret_item = retrieved.line_items[0]
    assert ret_item.id == item_id
    assert ret_item.description == "Development Services"
    assert ret_item.quantity == Decimal("15.5")
    assert ret_item.unit_price.amount == Decimal("100.00")
    assert ret_item.discount.amount == Decimal("50.00")
    assert ret_item.tax.amount == Decimal("10.00")
    assert ret_item.amount.amount == Decimal("1500.00")

    # Check adjustments
    assert len(retrieved.adjustments) == 1
    assert retrieved.adjustments[0].description == "Promo Discount"
    assert retrieved.adjustments[0].amount.amount == Decimal("15.00")

    # Check tax lines
    assert len(retrieved.taxes) == 1
    assert retrieved.taxes[0].code == "VAT-10"
    assert retrieved.taxes[0].amount.amount == Decimal("150.00")
    assert retrieved.taxes[0].rate == Decimal("0.10")

    # Check outbox events - InvoiceCreated should exist
    outbox_res = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "InvoiceCreated")
    )
    events = outbox_res.scalars().all()
    assert len(events) == 1
    event_payload = json.loads(events[0].payload)
    assert event_payload["invoice_id"] == str(inv_id.value)
    assert event_payload["organization_id"] == str(org_id.value)


@pytest.mark.asyncio
async def test_invoice_issue_and_immutability(db_session: AsyncSession):
    # Prerequisite Organization
    org_repo = PostgresOrganizationRepository(db_session)
    org_id = OrganizationId.generate()
    org = Organization.create(
        id=org_id,
        name=OrganizationName("Issuer Org"),
        slug=OrganizationSlug("issuer-org"),
        owner_id=UserId.generate(),
        settings=OrganizationSettings("USD")
    ).value
    await org_repo.save(org)
    
    invoice_repo = PostgresInvoiceRepository(db_session)

    # Create & persist Draft
    inv_id = InvoiceId.generate()
    usd = Currency("USD", CurrencyType.FIAT)
    terms = PaymentTerms(PaymentTermsType.NET_30)
    buyer = BillingSnapshot("Test Buyer", "b@b.com", "Main Road", "US")

    invoice = Invoice.create(inv_id, org_id, InvoiceOrigin(InvoiceOriginType.NATIVE), usd, terms, billing_snapshot=buyer).value
    item = InvoiceLineItem(LineItemId.generate(), "Services", Decimal("1"), Money("100.00", usd))
    invoice.add_line_item(item)
    await invoice_repo.save(invoice)
    await db_session.commit()

    # Retrieve and issue
    retrieved = await invoice_repo.find_by_id(inv_id)
    assert retrieved is not None
    
    inv_num = InvoiceNumber("INV-2026-99")
    issue_time = datetime(2026, 8, 15, 12, 0, 0)
    issue_res = retrieved.issue(inv_num, issued_at=issue_time)
    assert issue_res.is_success

    # Save Issued Invoice
    await invoice_repo.save(retrieved)
    await db_session.commit()

    # Retrieve back and check status/due date
    retrieved_issued = await invoice_repo.find_by_id(inv_id)
    assert retrieved_issued is not None
    assert retrieved_issued.lifecycle_status == InvoiceStatus.ISSUED
    assert retrieved_issued.invoice_number == inv_num
    assert retrieved_issued.issued_at == issue_time
    assert retrieved_issued.due_at == issue_time + timedelta(days=30)

    # Check InvoiceIssued outbox event
    outbox_res = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "InvoiceIssued")
    )
    events = outbox_res.scalars().all()
    assert len(events) == 1
    event_payload = json.loads(events[0].payload)
    assert event_payload["invoice_id"] == str(inv_id.value)
    assert event_payload["invoice_number"] == "INV-2026-99"

    # Verify delete policy: Deleting an ISSUED invoice must raise ValueError
    with pytest.raises(ValueError) as exc_info:
        await invoice_repo.delete(inv_id)
    assert "Audit Trail Protection" in str(exc_info.value)


@pytest.mark.asyncio
async def test_draft_deletion(db_session: AsyncSession):
    org_repo = PostgresOrganizationRepository(db_session)
    org_id = OrganizationId.generate()
    org = Organization.create(org_id, OrganizationName("Draft Org"), OrganizationSlug("draft-org"), UserId.generate(), OrganizationSettings("USD")).value
    await org_repo.save(org)

    invoice_repo = PostgresInvoiceRepository(db_session)
    inv_id = InvoiceId.generate()
    invoice = Invoice.create(inv_id, org_id, InvoiceOrigin(InvoiceOriginType.NATIVE), Currency("USD", CurrencyType.FIAT), PaymentTerms(PaymentTermsType.NET_30)).value
    
    await invoice_repo.save(invoice)
    await db_session.commit()

    # Deleting DRAFT invoice is allowed
    await invoice_repo.delete(inv_id)
    await db_session.commit()

    # Check that it is deleted
    exists = await invoice_repo.exists(inv_id)
    assert exists is False
