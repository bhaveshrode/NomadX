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
from nomadx.src.domain.invoice import InvoiceId, LineItemId, BillingSnapshot, CustomerId

# Application imports
from nomadx.src.application.invoice import (
    ExecutionContext,
    CreateInvoiceCommand,
    CreateInvoiceHandler,
    AddInvoiceLineItemCommand,
    AddInvoiceLineItemHandler,
    UpdateInvoiceLineItemCommand,
    UpdateInvoiceLineItemHandler,
    RemoveInvoiceLineItemCommand,
    RemoveInvoiceLineItemHandler,
    AddInvoiceTaxLineCommand,
    AddInvoiceTaxLineHandler,
    RemoveInvoiceTaxLineCommand,
    RemoveInvoiceTaxLineHandler,
    AddInvoiceAdjustmentCommand,
    AddInvoiceAdjustmentHandler,
    RemoveInvoiceAdjustmentCommand,
    RemoveInvoiceAdjustmentHandler,
    IssueInvoiceCommand,
    IssueInvoiceHandler,
    VoidInvoiceCommand,
    VoidInvoiceHandler
)

# Infrastructure imports
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel


@pytest.fixture
async def test_org(db_session: AsyncSession) -> Organization:
    org_repo = PostgresOrganizationRepository(db_session)
    org_id = OrganizationId.generate()
    org = Organization.create(
        id=org_id,
        name=OrganizationName("App Test Org"),
        slug=OrganizationSlug("app-test-org"),
        owner_id=UserId.generate(),
        settings=OrganizationSettings("USD")
    ).value
    await org_repo.save(org)
    await db_session.commit()
    return org


@pytest.fixture
def org_ctx(test_org: Organization) -> ExecutionContext:
    return ExecutionContext(
        user_id="user-123",
        organization_id=test_org.id.value,
        role="OWNER"
    )


@pytest.fixture
def foreign_ctx() -> ExecutionContext:
    return ExecutionContext(
        user_id="user-456",
        organization_id=OrganizationId.generate().value,
        role="MEMBER"
    )


@pytest.mark.asyncio
async def test_create_invoice_success(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    handler = CreateInvoiceHandler(repo)

    inv_id = str(InvoiceId.generate().value)
    cmd = CreateInvoiceCommand(
        invoice_id=inv_id,
        origin_type="NATIVE",
        source_system=None,
        external_invoice_id=None,
        currency_code="USD",
        currency_type="FIAT",
        payment_terms_type="NET_30",
        customer_id=str(UserId.generate().value),
        billing_snapshot={
            "legal_name": "Buyer Corp",
            "email": "buyer@corp.com",
            "address": "123 Main St",
            "country": "US",
            "tax_identifier": "TAX-9988"
        }
    )

    res = await handler.handle(cmd, org_ctx)
    assert res.is_success
    await db_session.commit()
    
    dto = res.value
    assert dto["id"] == inv_id
    assert dto["organization_id"] == org_ctx.organization_id
    assert dto["lifecycle_status"] == "DRAFT"
    assert dto["billing_snapshot"]["legal_name"] == "Buyer Corp"
    assert dto["totals"]["grand_total"] == "0.00"

    # Verify persisted
    persisted = await repo.find_by_id(InvoiceId(inv_id))
    assert persisted is not None
    assert persisted.organization_id.value == org_ctx.organization_id


@pytest.mark.asyncio
async def test_create_invoice_tenant_mismatch(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    handler = CreateInvoiceHandler(repo)

    inv_id = str(InvoiceId.generate().value)
    cmd = CreateInvoiceCommand(
        invoice_id=inv_id,
        origin_type="NATIVE",
        source_system=None,
        external_invoice_id=None,
        currency_code="USD",
        currency_type="FIAT",
        payment_terms_type="NET_30",
        organization_id="foreign-org-id"
    )

    res = await handler.handle(cmd, org_ctx)
    assert res.is_failure
    assert res.error.code == "UNAUTHORIZED_ACCESS"


@pytest.mark.asyncio
async def test_line_item_crud_flow(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    
    # 1. Create Invoice
    inv_id = str(InvoiceId.generate().value)
    create_cmd = CreateInvoiceCommand(
        invoice_id=inv_id,
        origin_type="NATIVE",
        source_system=None,
        external_invoice_id=None,
        currency_code="USD",
        currency_type="FIAT",
        payment_terms_type="NET_30"
    )
    await CreateInvoiceHandler(repo).handle(create_cmd, org_ctx)
    await db_session.commit()

    # 2. Add Line Item
    item_id = str(LineItemId.generate().value)
    add_cmd = AddInvoiceLineItemCommand(
        invoice_id=inv_id,
        line_item_id=item_id,
        description="Software Development",
        quantity=Decimal("5.5"),
        unit_price=Decimal("120.00"),
        discount=Decimal("20.00"),
        tax=Decimal("10.00")
    )
    add_res = await AddInvoiceLineItemHandler(repo).handle(add_cmd, org_ctx)
    assert add_res.is_success
    await db_session.commit()
    
    # Net amount calculation: 5.5 * 120 - 20 = 640.000
    dto = add_res.value
    assert len(dto["line_items"]) == 1
    assert dto["line_items"][0]["amount"] == "640.000"
    assert dto["totals"]["subtotal"] == "640.000"
    assert dto["totals"]["tax_total"] == "10.00"
    assert dto["totals"]["grand_total"] == "650.000"

    # 3. Update Line Item
    upd_cmd = UpdateInvoiceLineItemCommand(
        invoice_id=inv_id,
        line_item_id=item_id,
        description="Senior Software Development",
        quantity=Decimal("10"),
        unit_price=Decimal("150.00"),
        discount=Decimal("100.00"),
        tax=Decimal("50.00")
    )
    upd_res = await UpdateInvoiceLineItemHandler(repo).handle(upd_cmd, org_ctx)
    assert upd_res.is_success
    await db_session.commit()
    
    # Net amount calculation: 10 * 150 - 100 = 1400.00
    dto = upd_res.value
    assert dto["line_items"][0]["description"] == "Senior Software Development"
    assert dto["line_items"][0]["amount"] == "1400.00"
    assert dto["totals"]["subtotal"] == "1400.00"
    assert dto["totals"]["tax_total"] == "50.00"
    assert dto["totals"]["grand_total"] == "1450.00"

    # 4. Remove Line Item
    rem_cmd = RemoveInvoiceLineItemCommand(invoice_id=inv_id, line_item_id=item_id)
    rem_res = await RemoveInvoiceLineItemHandler(repo).handle(rem_cmd, org_ctx)
    assert rem_res.is_success
    await db_session.commit()
    assert len(rem_res.value["line_items"]) == 0
    assert rem_res.value["totals"]["grand_total"] == "0.00"


@pytest.mark.asyncio
async def test_add_and_remove_taxes_adjustments(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    
    # Setup Draft Invoice
    inv_id = str(InvoiceId.generate().value)
    await CreateInvoiceHandler(repo).handle(
        CreateInvoiceCommand(inv_id, "NATIVE", None, None, "USD", "FIAT", "NET_30"),
        org_ctx
    )
    await db_session.commit()

    # Add a base line item
    await AddInvoiceLineItemHandler(repo).handle(
        AddInvoiceLineItemCommand(inv_id, str(LineItemId.generate().value), "Consulting", Decimal("2"), Decimal("100.00")),
        org_ctx
    )
    await db_session.commit()

    # 1. Add custom tax line
    tax_cmd = AddInvoiceTaxLineCommand(
        invoice_id=inv_id,
        code="VAT-20",
        name="Value Added Tax",
        rate=Decimal("0.20"),
        taxable_amount=Decimal("200.00"),
        amount=Decimal("40.00")
    )
    tax_res = await AddInvoiceTaxLineHandler(repo).handle(tax_cmd, org_ctx)
    assert tax_res.is_success
    await db_session.commit()

    dto = tax_res.value
    assert len(dto["taxes"]) == 1
    assert dto["totals"]["tax_total"] == "40.00"
    assert dto["totals"]["grand_total"] == "240.00"

    # 2. Add discount adjustment
    adj_cmd = AddInvoiceAdjustmentCommand(
        invoice_id=inv_id,
        type="DISCOUNT",
        description="Partner Discount",
        amount=Decimal("25.00")
    )
    adj_res = await AddInvoiceAdjustmentHandler(repo).handle(adj_cmd, org_ctx)
    assert adj_res.is_success
    await db_session.commit()

    dto = adj_res.value
    assert len(dto["adjustments"]) == 1
    assert dto["totals"]["discount_total"] == "25.00"
    assert dto["totals"]["grand_total"] == "215.00" # 200 - 25 + 40 = 215.00

    # 3. Remove tax line
    rem_tax_cmd = RemoveInvoiceTaxLineCommand(inv_id, "VAT-20", "Value Added Tax", Decimal("0.20"), Decimal("200.00"), Decimal("40.00"))
    rem_tax_res = await RemoveInvoiceTaxLineHandler(repo).handle(rem_tax_cmd, org_ctx)
    assert rem_tax_res.is_success
    await db_session.commit()

    assert len(rem_tax_res.value["taxes"]) == 0
    assert rem_tax_res.value["totals"]["grand_total"] == "175.00" # 200 - 25 = 175


@pytest.mark.asyncio
async def test_lifecycle_issue_and_void(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    
    # 1. Setup Invoice
    inv_id = str(InvoiceId.generate().value)
    await CreateInvoiceHandler(repo).handle(
        CreateInvoiceCommand(inv_id, "NATIVE", None, None, "USD", "FIAT", "NET_30"),
        org_ctx
    )
    await db_session.commit()

    # Attempt to issue empty invoice (fails)
    issue_res = await IssueInvoiceHandler(repo).handle(IssueInvoiceCommand(inv_id, "INV-1"), org_ctx)
    assert issue_res.is_failure
    assert "satisfy requirements" in issue_res.error.message.lower()

    # Add line item
    await AddInvoiceLineItemHandler(repo).handle(
        AddInvoiceLineItemCommand(inv_id, str(LineItemId.generate().value), "Item", Decimal("1"), Decimal("50.00")),
        org_ctx
    )
    await db_session.commit()

    # Attempt to issue without billing snapshot (fails)
    issue_res = await IssueInvoiceHandler(repo).handle(IssueInvoiceCommand(inv_id, "INV-1"), org_ctx)
    assert issue_res.is_failure

    # Add billing snapshot
    invoice = await repo.find_by_id(InvoiceId(inv_id))
    buyer = BillingSnapshot("Buyer legal", "buyer@buyer.com", "Address", "US")
    invoice.update_customer(CustomerId.generate(), buyer)
    await repo.save(invoice)
    await db_session.commit()

    # Issue successfully
    issue_time = datetime(2026, 8, 15, 12, 0, 0)
    issue_res = await IssueInvoiceHandler(repo).handle(IssueInvoiceCommand(inv_id, "INV-100", issue_time), org_ctx)
    assert issue_res.is_success
    await db_session.commit()

    assert issue_res.value["lifecycle_status"] == "ISSUED"
    assert issue_res.value["issued_at"] == issue_time.isoformat()

    # Verify duplicate invoice number check
    inv_id_2 = str(InvoiceId.generate().value)
    await CreateInvoiceHandler(repo).handle(
        CreateInvoiceCommand(inv_id_2, "NATIVE", None, None, "USD", "FIAT", "NET_30", billing_snapshot={
            "legal_name": "Buyer legal", "email": "buyer@buyer.com", "address": "Address", "country": "US"
        }),
        org_ctx
    )
    await db_session.commit()

    await AddInvoiceLineItemHandler(repo).handle(
        AddInvoiceLineItemCommand(inv_id_2, str(LineItemId.generate().value), "Item", Decimal("1"), Decimal("50.00")),
        org_ctx
    )
    await db_session.commit()

    # Attempt duplicate issue (fails)
    dup_res = await IssueInvoiceHandler(repo).handle(IssueInvoiceCommand(inv_id_2, "INV-100"), org_ctx)
    assert dup_res.is_failure
    assert dup_res.error.code == "DUPLICATE_INVOICE_NUMBER"

    # Cannot edit issued invoice
    edit_res = await AddInvoiceLineItemHandler(repo).handle(
        AddInvoiceLineItemCommand(inv_id, str(LineItemId.generate().value), "Item 2", Decimal("1"), Decimal("10.00")),
        org_ctx
    )
    assert edit_res.is_failure
    assert edit_res.error.code == "INVOICE_NOT_EDITABLE"

    # Void issued invoice
    void_res = await VoidInvoiceHandler(repo).handle(VoidInvoiceCommand(inv_id), org_ctx)
    assert void_res.is_success
    await db_session.commit()
    assert void_res.value["lifecycle_status"] == "VOIDED"


@pytest.mark.asyncio
async def test_tenant_isolation_violations(db_session: AsyncSession, org_ctx: ExecutionContext, foreign_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    
    # Org A creates invoice
    inv_id = str(InvoiceId.generate().value)
    await CreateInvoiceHandler(repo).handle(
        CreateInvoiceCommand(inv_id, "NATIVE", None, None, "USD", "FIAT", "NET_30"),
        org_ctx
    )
    await db_session.commit()

    # Org B attempts to add line item to A's invoice (denied)
    add_item_handler = AddInvoiceLineItemHandler(repo)
    res = await add_item_handler.handle(
        AddInvoiceLineItemCommand(inv_id, str(LineItemId.generate().value), "Steal", Decimal("100"), Decimal("1000.00")),
        foreign_ctx
    )
    assert res.is_failure
    assert res.error.code == "UNAUTHORIZED_ACCESS"

    # Org B attempts to issue A's invoice (denied)
    issue_handler = IssueInvoiceHandler(repo)
    res2 = await issue_handler.handle(IssueInvoiceCommand(inv_id, "INV-HACK"), foreign_ctx)
    assert res2.is_failure
    assert res2.error.code == "UNAUTHORIZED_ACCESS"

    # Org B attempts to void A's invoice (denied)
    void_handler = VoidInvoiceHandler(repo)
    res3 = await void_handler.handle(VoidInvoiceCommand(inv_id), foreign_ctx)
    assert res3.is_failure
    assert res3.error.code == "UNAUTHORIZED_ACCESS"


@pytest.mark.asyncio
async def test_outbox_integration_via_handlers(db_session: AsyncSession, org_ctx: ExecutionContext):
    repo = PostgresInvoiceRepository(db_session)
    
    inv_id = str(InvoiceId.generate().value)
    await CreateInvoiceHandler(repo).handle(
        CreateInvoiceCommand(inv_id, "NATIVE", None, None, "USD", "FIAT", "NET_30"),
        org_ctx
    )
    await db_session.commit()

    # Verify OutboxEventDbModel has the event
    outbox_res = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "InvoiceCreated")
    )
    events = outbox_res.scalars().all()
    assert len(events) == 1
    event_payload = json.loads(events[0].payload)
    assert event_payload["invoice_id"] == inv_id
