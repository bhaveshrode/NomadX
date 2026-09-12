import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

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

from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivablePaymentStatus, ReceivableCollectionStatus
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository,
    PostgresReceivableRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel


async def _setup_merchant_and_invoice(session: AsyncSession, org_id_str: str, inv_id_str: str) -> Invoice:
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("AR Test Corp"),
        slug=OrganizationSlug(f"ar-test-{org_id_str[:8]}"),
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
    await invoice_repo.save(invoice)
    await session.flush()
    return invoice


@pytest.mark.asyncio
async def test_receivable_persistence_lifecycle(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    cust_id = str(uuid4())
    
    # 1. Setup base merchant & invoice
    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    # 2. Create Receivable aggregate
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    # High-precision Decimal test
    original_amount = Money(Decimal("10000.123456"), currency)
    due_date = datetime.utcnow() + timedelta(days=30)
    
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=original_amount,
        due_date=due_date
    ).value

    # 3. Save to database
    repo = PostgresReceivableRepository(db_session)
    await repo.save(receivable)
    await db_session.flush()

    # Clear session to force fresh select query
    db_session.expunge_all()

    # 4. Retrieve & Reconstruct
    retrieved = await repo.get_by_id(ReceivableId(rec_id))
    assert retrieved is not None
    assert retrieved.id.value == rec_id
    assert retrieved.organization_id.value == org_id
    assert retrieved.customer_id.value == cust_id
    assert retrieved.invoice_id.value == inv_id
    assert retrieved.original_amount.amount == Decimal("10000.123456")
    assert retrieved.paid_amount.amount == Decimal("0.00")
    assert retrieved.outstanding_amount.amount == Decimal("10000.123456")
    assert retrieved.payment_status == ReceivablePaymentStatus.UNPAID
    assert retrieved.collection_status == ReceivableCollectionStatus.CURRENT
    
    # 5. Apply allocation, save & verify transitions
    alloc = Money(Decimal("3000.02"), currency)
    retrieved.apply_payment_allocation(alloc)
    await repo.save(retrieved)
    await db_session.flush()
    db_session.expunge_all()

    updated = await repo.get_by_id(ReceivableId(rec_id))
    assert updated.paid_amount.amount == Decimal("3000.02")
    assert updated.outstanding_amount.amount == Decimal("7000.103456")
    assert updated.payment_status == ReceivablePaymentStatus.PARTIALLY_PAID


@pytest.mark.asyncio
async def test_receivable_uniqueness_constraint(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id1 = str(uuid4())
    rec_id2 = str(uuid4())
    
    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)
    
    # Create first receivable
    rec1 = Receivable.create(
        id=ReceivableId(rec_id1),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await repo.save(rec1)
    await db_session.flush()

    # Create second receivable pointing to SAME invoice
    rec2 = Receivable.create(
        id=ReceivableId(rec_id2),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value

    # Attempting to save second receivable violates unique index constraint
    with pytest.raises(IntegrityError):
        await repo.save(rec2)
        await db_session.flush()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_receivable_tenant_isolation(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    inv_a = str(uuid4())
    rec_a = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_a, inv_a)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    rec = Receivable.create(
        id=ReceivableId(rec_a),
        organization_id=OrganizationId(org_a),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_a),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await repo.save(rec)
    await db_session.flush()

    # Find by org a
    list_a = await repo.find_by_organization(OrganizationId(org_a))
    assert len(list_a) == 1
    assert list_a[0].id.value == rec_a

    # Find by org b (should be isolated/empty)
    list_b = await repo.find_by_organization(OrganizationId(org_b))
    assert len(list_b) == 0


@pytest.mark.asyncio
async def test_receivable_outbox_integration(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    rec = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value
    
    await repo.save(rec)
    await db_session.flush()

    # Query outbox events table
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableCreated")
    res = await db_session.execute(stmt)
    outbox_event = res.scalar_one_or_none()

    assert outbox_event is not None
    assert "ReceivableCreated" in outbox_event.event_type
    assert outbox_event.processed is False


@pytest.mark.asyncio
async def test_receivable_atomic_rollback(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    rec = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value

    # Intentionally save, but force duplicate constraint to cause rollback
    await repo.save(rec)
    
    # Save a second one with same invoice_id
    rec_dup = Receivable.create(
        id=ReceivableId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value

    try:
        await repo.save(rec_dup)
        await db_session.flush()
    except IntegrityError:
        await db_session.rollback()

    # Verify original does NOT exist in database after rollback
    retrieved = await repo.get_by_id(ReceivableId(rec_id))
    assert retrieved is None


@pytest.mark.asyncio
async def test_receivable_concurrency_locking(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    rec = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value
    await repo.save(rec)
    await db_session.flush()

    # Query with FOR UPDATE lock
    locked_rec = await repo.get_by_id(ReceivableId(rec_id), lock=True)
    assert locked_rec is not None
    assert locked_rec.id.value == rec_id


@pytest.mark.asyncio
async def test_receivable_deletion_rejected(db_session: AsyncSession):
    repo = PostgresReceivableRepository(db_session)
    with pytest.raises(ValueError, match="Audit Trail Protection"):
        await repo.delete(ReceivableId(str(uuid4())))


@pytest.mark.asyncio
async def test_receivable_constraints_detailed(db_session: AsyncSession):
    from nomadx.src.domain.receivable.value_objects import ReceivableReference, ReceivableIdempotencyKey

    org_id1 = str(uuid4())
    org_id2 = str(uuid4())
    inv_id1 = str(uuid4())
    inv_id2 = str(uuid4())
    inv_id3 = str(uuid4())
    rec_id1 = str(uuid4())
    rec_id2 = str(uuid4())
    rec_id3 = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id1, inv_id1)
    await _setup_merchant_and_invoice(db_session, org_id2, inv_id2)
    await _setup_merchant_and_invoice(db_session, org_id1, inv_id3)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    # 1. Create first receivable
    rec1 = Receivable.create(
        id=ReceivableId(rec_id1),
        organization_id=OrganizationId(org_id1),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id1),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow(),
        reference=ReceivableReference("AR-UNIQUE-REF-1"),
        idempotency_key=ReceivableIdempotencyKey("idem-key-1")
    ).value
    await repo.save(rec1)
    await db_session.flush()

    # 2. Attempt duplicate reference globally (even on diff org)
    rec2 = Receivable.create(
        id=ReceivableId(rec_id2),
        organization_id=OrganizationId(org_id2),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id2),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow(),
        reference=ReceivableReference("AR-UNIQUE-REF-1"),
        idempotency_key=ReceivableIdempotencyKey("idem-key-2")
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_RECEIVABLE_REFERENCE"):
        async with db_session.begin_nested():
            await repo.save(rec2)
            await db_session.flush()

    # 3. Attempt duplicate idempotency key on same org
    rec3 = Receivable.create(
        id=ReceivableId(rec_id3),
        organization_id=OrganizationId(org_id1),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_id3),
        original_amount=Money(Decimal("300.00"), currency),
        due_date=datetime.utcnow(),
        reference=ReceivableReference("AR-UNIQUE-REF-3"),
        idempotency_key=ReceivableIdempotencyKey("idem-key-1")
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_RECEIVABLE_IDEMPOTENCY"):
        async with db_session.begin_nested():
            await repo.save(rec3)
            await db_session.flush()


@pytest.mark.asyncio
async def test_receivable_allocations_and_dunning_persistence(db_session: AsyncSession):
    from nomadx.src.domain.receivable.dunning import DunningStage
    from nomadx.src.infrastructure.database.models import ReceivableAllocationDbModel

    org_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())
    cust_id = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    # 1. Create and save
    rec = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("1000.00"), currency),
        due_date=datetime.utcnow()
    ).value

    # Apply allocations
    res1 = rec.allocate_payment(
        payment_id="pay-1",
        amount=Money(Decimal("300.00"), currency),
        allocation_reference="alloc-id-1",
        occurred_on=datetime(2026, 8, 20)
    )
    assert res1.is_success

    res2 = rec.allocate_payment(
        payment_id="pay-2",
        amount=Money(Decimal("200.00"), currency),
        allocation_reference="alloc-id-2",
        occurred_on=datetime(2026, 8, 21)
    )
    assert res2.is_success

    # Advance dunning stage
    res_dun = rec.advance_dunning_stage(DunningStage.FIRST_REMINDER, occurred_on=datetime(2026, 8, 22))
    assert res_dun.is_success

    await repo.save(rec)
    await db_session.flush()

    # Clear to force fresh select
    db_session.expunge_all()

    # Reconstruct aggregate
    retrieved = await repo.get_by_id(ReceivableId(rec_id))
    assert retrieved is not None
    assert retrieved.dunning_state.stage == DunningStage.FIRST_REMINDER
    assert retrieved.dunning_state.attempts == 1
    assert len(retrieved.allocations) == 2

    # Check allocations sorting / values
    allocs = sorted(retrieved.allocations, key=lambda a: a.payment_id)
    assert allocs[0].payment_id == "pay-1"
    assert allocs[0].allocated_amount.amount == Decimal("300.00")
    assert allocs[0].allocation_reference == "alloc-id-1"
    assert allocs[1].payment_id == "pay-2"
    assert allocs[1].allocated_amount.amount == Decimal("200.00")
    assert allocs[1].allocation_reference == "alloc-id-2"

    # Verify allocations table directly
    stmt = select(ReceivableAllocationDbModel).where(ReceivableAllocationDbModel.receivable_id == rec_id)
    res_db = await db_session.execute(stmt)
    db_allocs = res_db.scalars().all()
    assert len(db_allocs) == 2


@pytest.mark.asyncio
async def test_receivable_allocations_tenant_isolation(db_session: AsyncSession):
    from nomadx.src.infrastructure.database.models import ReceivableAllocationDbModel

    org_a = str(uuid4())
    org_b = str(uuid4())
    inv_a = str(uuid4())
    rec_a = str(uuid4())

    await _setup_merchant_and_invoice(db_session, org_a, inv_a)

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    repo = PostgresReceivableRepository(db_session)

    rec = Receivable.create(
        id=ReceivableId(rec_a),
        organization_id=OrganizationId(org_a),
        customer_id=CustomerId(str(uuid4())),
        invoice_id=InvoiceId(inv_a),
        original_amount=Money(Decimal("500.00"), currency),
        due_date=datetime.utcnow()
    ).value

    rec.allocate_payment(
        payment_id="pay-a",
        amount=Money(Decimal("100.00"), currency),
        allocation_reference="alloc-a",
        occurred_on=datetime.utcnow()
    )

    await repo.save(rec)
    await db_session.flush()

    # Query allocations for Org A
    stmt_a = select(ReceivableAllocationDbModel).where(
        ReceivableAllocationDbModel.organization_id == org_a
    )
    res_a = await db_session.execute(stmt_a)
    assert len(res_a.scalars().all()) == 1

    # Query allocations for Org B (isolated)
    stmt_b = select(ReceivableAllocationDbModel).where(
        ReceivableAllocationDbModel.organization_id == org_b
    )
    res_b = await db_session.execute(stmt_b)
    assert len(res_b.scalars().all()) == 0
