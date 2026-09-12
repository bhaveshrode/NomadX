import pytest
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    UserId,
    OrganizationSettings,
)
from nomadx.src.domain.invoice import InvoiceId, BillingSnapshot, CustomerId, Invoice, InvoiceOrigin, InvoiceOriginType, PaymentTerms, PaymentTermsType
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.domain.payment import (
    PaymentId, PaymentIntentId, PaymentIntent, PaymentIntentStatus, PaymentAsset,
    PaymentDestination, PaymentMethod, Payment, PaymentStatus, PaymentAllocationId, PaymentAllocation
)

from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivablePaymentStatus, ReceivableCollectionStatus
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository,
    PostgresReceivableRepository,
    PostgresPaymentRepository,
    PostgresPaymentIntentRepository,
    PostgresPaymentAllocationRepository,
    PostgresCollectionActionRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel
from nomadx.src.application.receivable.commands import (
    CreateReceivableCommand,
    ApplyPaymentAllocationCommand,
    AllocatePaymentCommand,
    MarkReceivableOverdueCommand,
    SettleReceivableCommand,
    WriteOffReceivableCommand,
    AdvanceDunningStageCommand,
    UpdateReceivableAgingCommand
)
from nomadx.src.application.receivable.handlers import (
    CreateReceivableHandler,
    ApplyPaymentAllocationHandler,
    AllocatePaymentHandler,
    MarkReceivableOverdueHandler,
    SettleReceivableHandler,
    WriteOffReceivableHandler,
    AdvanceDunningStageHandler,
    UpdateReceivableAgingHandler
)
from nomadx.src.infrastructure.workers.receivable_worker import ReceivableWorker
from nomadx.src.presentation.api.dependencies.context import ExecutionContext


async def _setup_merchant_and_invoice(session: AsyncSession, org_id_str: str, inv_id_str: str, amount: Decimal = Decimal("10000.00")) -> Invoice:
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("AR App Corp"),
        slug=OrganizationSlug(f"ar-app-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings("USDC")
    ).value
    await org_repo.save(org)

    invoice_repo = PostgresInvoiceRepository(session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    
    invoice = Invoice.create(
        id=InvoiceId(inv_id_str),
        organization_id=OrganizationId(org_id_str),
        origin=InvoiceOrigin(InvoiceOriginType.NATIVE),
        customer_id=CustomerId(str(uuid4())),
        currency=currency,
        payment_terms=PaymentTerms(PaymentTermsType.NET_30),
        billing_snapshot=BillingSnapshot(
            legal_name="Client Corp",
            email="client@corp.com",
            address="456 client ave",
            country="US"
        )
    ).value
    # Add a line item to match target amount
    from nomadx.src.domain.invoice.line_item import InvoiceLineItem, LineItemId
    line_item = InvoiceLineItem(
        id=LineItemId(str(uuid4())),
        description="Consulting Services",
        quantity=Decimal("1"),
        unit_price=Money(amount, currency)
    )
    from nomadx.src.domain.invoice.value_objects import InvoiceNumber
    invoice.add_line_item(line_item)
    issue_res = invoice.issue(InvoiceNumber(f"INV-{org_id_str[:8]}-{inv_id_str[:8]}"), datetime.utcnow())
    assert issue_res.is_success, f"Failed to issue invoice: {issue_res.error.message}"
    await invoice_repo.save(invoice)
    await session.flush()
    return invoice


@pytest.mark.asyncio
async def test_create_receivable_from_invoice_issued(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("5000.00"))

    receivable_repo = PostgresReceivableRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)

    handler = CreateReceivableHandler(receivable_repo, invoice_repo)
    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    cmd = CreateReceivableCommand(
        receivable_id=str(uuid4()),
        organization_id=org_id,
        invoice_id=inv_id
    )

    # 1. Execute Handler
    res = await handler.handle(cmd, ctx)
    assert res.is_success
    receivable = res.value

    assert receivable.original_amount.amount == Decimal("5000.00")
    assert receivable.payment_status == ReceivablePaymentStatus.UNPAID
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT

    # 2. Duplicate Execution (Idempotency check)
    cmd_dup = CreateReceivableCommand(
        receivable_id=str(uuid4()), # different ID, same invoice
        organization_id=org_id,
        invoice_id=inv_id
    )
    res_dup = await handler.handle(cmd_dup, ctx)
    assert res_dup.is_success
    assert res_dup.value.id.value == receivable.id.value # returns existing receivable


@pytest.mark.asyncio
async def test_apply_payment_allocation_handler(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    # Create Invoice
    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("10000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Receivable
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("10000.00"), currency),
        due_date=datetime.utcnow() + timedelta(days=30)
    ).value
    await receivable_repo.save(receivable)

    # Create Payment Allocation in ALLOCATED status
    allocation = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(pay_id),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("2000.00"), currency),
        status="ALLOCATED"
    ).value
    await allocation_repo.save(allocation)
    await db_session.flush()

    handler = ApplyPaymentAllocationHandler(receivable_repo, allocation_repo)
    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    # 1. Apply $2,000 allocation
    cmd = ApplyPaymentAllocationCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        payment_id=pay_id,
        allocation_id=alloc_id,
        allocated_amount=Decimal("2000.00"),
        currency_code="USDC"
    )
    res = await handler.handle(cmd, ctx)
    assert res.is_success
    assert res.value.paid_amount.amount == Decimal("2000.00")
    assert res.value.outstanding_amount.amount == Decimal("8000.00")
    assert res.value.payment_status == ReceivablePaymentStatus.PARTIALLY_PAID

    # Refresh allocation state
    db_session.expunge_all()
    alloc_refreshed = await allocation_repo.find_by_id(PaymentAllocationId(alloc_id))
    assert alloc_refreshed.status == "APPLIED"

    # 2. Duplicate Execution (Idempotency check: should not increase paid amount again)
    res_dup = await handler.handle(cmd, ctx)
    assert res_dup.is_success
    assert res_dup.value.paid_amount.amount == Decimal("2000.00") # remains 2000


@pytest.mark.asyncio
async def test_receivable_tenant_isolation_violations(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    inv_a = str(uuid4())
    rec_a = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_a, inv_a, Decimal("5000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Org A receivable
    rec = Receivable.create(
        id=ReceivableId(rec_a),
        organization_id=OrganizationId(org_a),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_a),
        original_amount=Money(Decimal("5000.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await receivable_repo.save(rec)

    # Create allocation belonging to Org B trying to update Org A receivable
    alloc = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(pay_id),
        invoice_id=InvoiceId(inv_a),
        organization_id=OrganizationId(org_b),
        allocated_amount=Money(Decimal("1000.00"), currency)
    ).value
    await allocation_repo.save(alloc)
    await db_session.flush()

    handler = ApplyPaymentAllocationHandler(receivable_repo, allocation_repo)
    
    # Try with Org B execution context -> fails due to context tenant mismatch
    ctx_b = ExecutionContext(user_id="worker", organization_id=org_b, role="SYSTEM")
    cmd = ApplyPaymentAllocationCommand(
        organization_id=org_a, # trying to target Org A
        receivable_id=rec_a,
        payment_id=pay_id,
        allocation_id=alloc_id,
        allocated_amount=Decimal("1000.00"),
        currency_code="USDC"
    )
    res_b = await handler.handle(cmd, ctx_b)
    assert res_b.is_failure
    assert "Context tenant mismatch" in res_b.error.message


@pytest.mark.asyncio
async def test_receivable_allocation_over_limit_fails(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("1000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Receivable with $1,000 original amount
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await receivable_repo.save(receivable)

    # Create Payment Allocation of $2,000 (exceeds outstanding)
    allocation = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(pay_id),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("2000.00"), currency)
    ).value
    await allocation_repo.save(allocation)
    await db_session.flush()

    handler = ApplyPaymentAllocationHandler(receivable_repo, allocation_repo)
    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    cmd = ApplyPaymentAllocationCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        payment_id=pay_id,
        allocation_id=alloc_id,
        allocated_amount=Decimal("2000.00"),
        currency_code="USDC"
    )
    res = await handler.handle(cmd, ctx)
    assert res.is_failure
    assert "exceeds outstanding balance" in res.error.message


@pytest.mark.asyncio
async def test_receivable_transactional_rollback_on_failure(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("1000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Receivable
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await receivable_repo.save(receivable)

    # Create Allocation
    allocation = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(pay_id),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("2000.00"), currency) # invalid too large amount
    ).value
    await allocation_repo.save(allocation)
    await db_session.commit()

    # Trigger worker/handler which fails due to domain rule, verifying rollback
    worker = ReceivableWorker()
    
    # Retrieve the outbox event automatically emitted by allocation_repo.save
    outbox_event = (await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "PaymentReconciled")
    )).scalar_one()

    # Process events. It should fail internally and roll back the transaction.
    await worker.process_pending_events()

    # Verify outbox event is marked as RETRYING or DEAD_LETTERED due to failure
    async with session_manager.session() as check_session:
        ev_check = (await check_session.execute(
            select(OutboxEventDbModel).where(OutboxEventDbModel.id == outbox_event.id)
        )).scalar_one()
        assert ev_check.status in ("RETRYING", "DEAD_LETTERED")
        assert "exceeds outstanding balance" in ev_check.error_message

    # Verify allocation status remains ALLOCATED and receivable paid remains 0
    async with session_manager.session() as check_session:
        alloc_repo = PostgresPaymentAllocationRepository(check_session)
        rec_repo = PostgresReceivableRepository(check_session)
        
        alloc_check = await alloc_repo.find_by_id(PaymentAllocationId(alloc_id))
        assert alloc_check.status == "ALLOCATED" # rolled back!

        rec_check = await rec_repo.get_by_id(ReceivableId(rec_id))
        assert rec_check.paid_amount.amount == Decimal("0.00") # rolled back!


@pytest.mark.asyncio
async def test_receivable_worker_e2e_synchronization(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    # 1. Setup invoice. This emits InvoiceIssued outbox event.
    invoice = await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("5000.00"))

    # Commit to flush outbox events to DB
    await db_session.commit()

    # 2. Run ReceivableWorker to consume InvoiceIssued -> Creates Receivable
    worker = ReceivableWorker()
    processed_issued = await worker.process_pending_events()
    assert processed_issued == 1

    # Verify Receivable exists
    async with session_manager.session() as check_session:
        rec_repo = PostgresReceivableRepository(check_session)
        receivable = await rec_repo.find_by_invoice_id(InvoiceId(inv_id))
        assert receivable is not None
        assert receivable.original_amount.amount == Decimal("5000.00")
        assert receivable.payment_status == ReceivablePaymentStatus.UNPAID

    # 3. Create payment allocation. This emits PaymentReconciled outbox event.
    async with session_manager.session() as alloc_session:
        allocation_repo = PostgresPaymentAllocationRepository(alloc_session)
        currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
        allocation = PaymentAllocation.create(
            id=PaymentAllocationId(alloc_id),
            payment_id=PaymentId(pay_id),
            invoice_id=InvoiceId(inv_id),
            organization_id=OrganizationId(org_id),
            allocated_amount=Money(Decimal("5000.00"), currency) # fully paid allocation
        ).value
        await allocation_repo.save(allocation)
        await alloc_session.commit()

    # 4. Run ReceivableWorker to consume PaymentReconciled -> Applies allocation to Receivable
    processed_reconciled = await worker.process_pending_events()
    assert processed_reconciled == 1

    # Verify Receivable is now fully paid and status is updated
    async with session_manager.session() as final_session:
        rec_repo = PostgresReceivableRepository(final_session)
        receivable = await rec_repo.find_by_invoice_id(InvoiceId(inv_id))
        assert receivable.paid_amount.amount == Decimal("5000.00")
        assert receivable.outstanding_amount.amount == Decimal("0.00")
        assert receivable.payment_status == ReceivablePaymentStatus.PAID
        assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED

        # Verify ReceivablePaid event created in Outbox events
        outbox_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivablePaid")
        outbox_res = await final_session.execute(outbox_stmt)
        events = outbox_res.scalars().all()
        assert len(events) == 1
        assert "ReceivablePaid" in events[0].event_type


@pytest.mark.asyncio
async def test_allocate_payment_handler_detailed(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("10000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Receivable
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("10000.00"), currency),
        due_date=datetime.utcnow() + timedelta(days=30)
    ).value
    await receivable_repo.save(receivable)

    # Create Payment Allocation
    allocation = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(pay_id),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("2000.00"), currency),
        status="ALLOCATED"
    ).value
    await allocation_repo.save(allocation)
    await db_session.flush()

    collection_action_repo = PostgresCollectionActionRepository(db_session)
    handler = AllocatePaymentHandler(receivable_repo, allocation_repo, collection_action_repo)
    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    # 1. Apply $2,000 allocation
    cmd = AllocatePaymentCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        payment_id=pay_id,
        allocated_amount=Decimal("2000.00"),
        currency_code="USDC",
        allocation_reference=alloc_id
    )
    res = await handler.handle(cmd, ctx)
    assert res.is_success
    assert res.value.paid_amount.amount == Decimal("2000.00")
    assert res.value.outstanding_amount.amount == Decimal("8000.00")

    # 2. Idempotency Check
    res_dup = await handler.handle(cmd, ctx)
    assert res_dup.is_success
    assert res_dup.value.paid_amount.amount == Decimal("2000.00")

    # 3. Allocation over limit fails
    alloc_id_large = str(uuid4())
    alloc_large = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id_large),
        payment_id=PaymentId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("15000.00"), currency),
        status="ALLOCATED"
    ).value
    await allocation_repo.save(alloc_large)
    await db_session.flush()

    cmd_large = AllocatePaymentCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        payment_id=alloc_large.payment_id.value,
        allocated_amount=Decimal("15000.00"),
        currency_code="USDC",
        allocation_reference=alloc_id_large
    )
    res_large = await handler.handle(cmd_large, ctx)
    assert res_large.is_failure
    assert "exceeds outstanding balance" in res_large.error.message or "ALLOCATION_EXCEEDS_OUTSTANDING" in res_large.error.code


@pytest.mark.asyncio
async def test_receivable_lifecycle_transitions(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("5000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)
    allocation_repo = PostgresPaymentAllocationRepository(db_session)

    # Create Receivable
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("5000.00"), currency),
        due_date=datetime.utcnow() - timedelta(days=1) # already overdue
    ).value
    await receivable_repo.save(receivable)
    await db_session.flush()

    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    # 1. Mark Overdue
    overdue_handler = MarkReceivableOverdueHandler(receivable_repo)
    overdue_cmd = MarkReceivableOverdueCommand(organization_id=org_id, receivable_id=rec_id)
    res_overdue = await overdue_handler.handle(overdue_cmd, ctx)
    assert res_overdue.is_success
    assert res_overdue.value.status.value == "OVERDUE"

    # 2. Write Off
    collection_action_repo = PostgresCollectionActionRepository(db_session)
    write_off_handler = WriteOffReceivableHandler(receivable_repo, collection_action_repo)
    write_off_cmd = WriteOffReceivableCommand(organization_id=org_id, receivable_id=rec_id, reason="Uncollectible debt")
    res_writeoff = await write_off_handler.handle(write_off_cmd, ctx)
    assert res_writeoff.is_success
    assert res_writeoff.value.status.value == "WRITTEN_OFF"

    # Attempt allocation after write-off fails
    alloc_handler = AllocatePaymentHandler(receivable_repo, allocation_repo, collection_action_repo)
    alloc_id = str(uuid4())
    allocation = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id),
        payment_id=PaymentId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("1000.00"), currency),
        status="ALLOCATED"
    ).value
    await allocation_repo.save(allocation)
    await db_session.flush()

    alloc_cmd = AllocatePaymentCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        payment_id=allocation.payment_id.value,
        allocated_amount=Decimal("1000.00"),
        currency_code="USDC",
        allocation_reference=alloc_id
    )
    res_alloc_fail = await alloc_handler.handle(alloc_cmd, ctx)
    assert res_alloc_fail.is_failure
    assert "written off" in res_alloc_fail.error.message or "RECEIVABLE_ALREADY_WRITTEN_OFF" in res_alloc_fail.error.code

    # 3. Settle
    # Create new receivable for settlement
    inv_id2 = str(uuid4())
    rec_id2 = str(uuid4())
    await _setup_merchant_and_invoice(db_session, org_id, inv_id2, Decimal("1000.00"))

    receivable2 = Receivable.create(
        id=ReceivableId(rec_id2),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id2),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow() + timedelta(days=30)
    ).value
    await receivable_repo.save(receivable2)
    await db_session.flush()

    settle_handler = SettleReceivableHandler(receivable_repo, collection_action_repo)
    settle_cmd = SettleReceivableCommand(organization_id=org_id, receivable_id=rec_id2)

    # Settlement fails when outstanding balance > 0
    res_settle_fail = await settle_handler.handle(settle_cmd, ctx)
    assert res_settle_fail.is_failure
    assert "outstanding balance" in res_settle_fail.error.message

    # Fully allocate
    alloc_id2 = str(uuid4())
    allocation2 = PaymentAllocation.create(
        id=PaymentAllocationId(alloc_id2),
        payment_id=PaymentId(str(uuid4())),
        invoice_id=InvoiceId(inv_id2),
        organization_id=OrganizationId(org_id),
        allocated_amount=Money(Decimal("1000.00"), currency),
        status="ALLOCATED"
    ).value
    await allocation_repo.save(allocation2)
    await db_session.flush()

    alloc_cmd2 = AllocatePaymentCommand(
        organization_id=org_id,
        receivable_id=rec_id2,
        payment_id=allocation2.payment_id.value,
        allocated_amount=Decimal("1000.00"),
        currency_code="USDC",
        allocation_reference=alloc_id2
    )
    await alloc_handler.handle(alloc_cmd2, ctx)

    # Settle succeeds now
    res_settle_success = await settle_handler.handle(settle_cmd, ctx)
    assert res_settle_success.is_success
    assert res_settle_success.value.status.value == "SETTLED"


@pytest.mark.asyncio
async def test_receivable_dunning_and_aging_use_cases(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("5000.00"))

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    # Create Receivable
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("5000.00"), currency),
        due_date=datetime.utcnow() - timedelta(days=1)
    ).value
    await receivable_repo.save(receivable)
    await db_session.flush()

    ctx = ExecutionContext(user_id="worker", organization_id=org_id, role="SYSTEM")

    # 1. Dunning progression
    dunning_handler = AdvanceDunningStageHandler(receivable_repo)
    dunning_cmd = AdvanceDunningStageCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        next_stage="FIRST_REMINDER"
    )
    res_dunning = await dunning_handler.handle(dunning_cmd, ctx)
    assert res_dunning.is_success
    assert res_dunning.value.dunning_state.stage.value == "FIRST_REMINDER"

    # Invalid dunning transition back to NONE
    dunning_cmd_invalid = AdvanceDunningStageCommand(
        organization_id=org_id,
        receivable_id=rec_id,
        next_stage="NONE"
    )
    res_dunning_invalid = await dunning_handler.handle(dunning_cmd_invalid, ctx)
    assert res_dunning_invalid.is_failure

    # 2. Aging update
    aging_handler = UpdateReceivableAgingHandler(receivable_repo)
    aging_cmd = UpdateReceivableAgingCommand(organization_id=org_id, receivable_id=rec_id)
    res_aging = await aging_handler.handle(aging_cmd, ctx)
    assert res_aging.is_success
