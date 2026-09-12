import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.settlement_reconciliation import (
    Settlement,
    SettlementStatus,
    SettlementEntry,
    SettlementId,
    SettlementReference,
    SettlementCurrency,
    SettlementAmount,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey,
    ReconciliationStatus,
    ReconciliationResult,
    ReconciliationDiscrepancy,
    AdjustmentType,
    SettlementAdjustment
)
from nomadx.src.infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
from nomadx.src.infrastructure.database.models import (
    SettlementReconciliationDbModel,
    SettlementEntryDbModel,
    SettlementAdjustmentDbModel,
    SettlementDiscrepancyDbModel,
    OutboxEventDbModel,
    OrganizationDbModel
)


@pytest.fixture
async def organization(db_session: AsyncSession) -> str:
    org_id = str(uuid4())
    org = OrganizationDbModel(
        id=org_id,
        name="Test Merchant Org",
        slug=f"test-merchant-org-{org_id[:8]}",
        status="ACTIVE",
        default_currency="USDC",
        owner_id=str(uuid4())
    )
    db_session.add(org)
    await db_session.commit()
    return org_id


@pytest.fixture
def repo(db_session: AsyncSession) -> PostgresSettlementRepository:
    return PostgresSettlementRepository(db_session)


@pytest.fixture
def base_setup(organization: str):
    org_id = OrganizationId(organization)
    ref = SettlementReference(f"SET-{uuid4().hex[:8].upper()}")
    merchant_ref = MerchantReference("MERCH-ORG-A")
    period = SettlementPeriod(
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 7, tzinfo=timezone.utc)
    )
    currency = SettlementCurrency("USDC")
    idem_key = SettlementIdempotencyKey(f"idem-{uuid4().hex[:12]}")
    
    return {
        "org_id": org_id,
        "ref": ref,
        "merchant_ref": merchant_ref,
        "period": period,
        "currency": currency,
        "idem_key": idem_key
    }


@pytest.mark.asyncio
async def test_repository_save_and_fetch(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Save to db
    await repo.save(settlement)
    await db_session.commit()

    # Clear session to force select query load
    db_session.expire_all()

    # Fetch back
    fetched = await repo.get_by_id(settlement_id)
    assert fetched is not None
    assert fetched.id.value == settlement_id.value
    assert fetched.organization_id.value == base_setup["org_id"].value
    assert fetched.reference.value == base_setup["ref"].value
    assert fetched.merchant_reference.value == base_setup["merchant_ref"].value
    assert fetched.period.start_date.replace(tzinfo=None) == base_setup["period"].start_date.replace(tzinfo=None)
    assert fetched.period.end_date.replace(tzinfo=None) == base_setup["period"].end_date.replace(tzinfo=None)
    assert fetched.currency.code == base_setup["currency"].code
    assert fetched.status == SettlementStatus.DRAFT
    assert fetched.reconciliation_state == ReconciliationStatus.PENDING
    assert fetched.idempotency_key.value == base_setup["idem_key"].value
    assert len(fetched.entries) == 0
    assert len(fetched.adjustments) == 0


@pytest.mark.asyncio
async def test_repository_save_with_entries_and_adjustments(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Create & Add entry 1
    entry1 = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("1000.00"),
        fees_amount=SettlementAmount("20.00"),
        adjustments_amount=SettlementAmount("10.00"),
        net_amount=SettlementAmount("970.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry1)

    # Create & Add entry 2
    entry2 = SettlementEntry(
        id=SettlementLineReference("LINE-ENT2"),
        source_transaction_reference="TX-102",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount("500.00"),
        fees_amount=SettlementAmount("5.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("495.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry2)

    # Add Adjustment
    adj = SettlementAdjustment(
        id="adj-fee-01",
        type=AdjustmentType.PROCESSING_FEE,
        description="Standard monthly platform charge",
        amount=SettlementAmount("50.00"),
        currency=base_setup["currency"]
    )
    settlement.apply_adjustment(adj)

    # Save to db
    await repo.save(settlement)
    await db_session.commit()
    db_session.expire_all()

    # Fetch back
    fetched = await repo.get_by_id(settlement_id)
    assert fetched is not None
    assert len(fetched.entries) == 2
    assert len(fetched.adjustments) == 1

    # Verify calculated totals
    assert fetched.gross_amount.amount == Decimal("1500.00")
    assert fetched.fees_amount.amount == Decimal("25.00")
    assert fetched.adjustments_amount.amount == Decimal("60.00") # 10 from entries + 50 aggregate adjustment
    assert fetched.net_amount.amount == Decimal("1415.00") # 1500 - 25 - 60 = 1415.00


@pytest.mark.asyncio
async def test_reconciliation_and_discrepancy_persistence(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("5.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("95.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry)
    settlement.open()
    settlement.start_reconciliation()

    # Reconcile with 1 discrepancy
    disc = ReconciliationDiscrepancy(
        entry_reference=SettlementLineReference("LINE-ENT1"),
        discrepancy_type="AMOUNT_MISMATCH",
        expected_amount=SettlementAmount("95.00"),
        actual_amount=SettlementAmount("90.00"),
        description=" short payment found"
    )
    result = ReconciliationResult(
        status=ReconciliationStatus.DISCREPANCY,
        discrepancies=[disc],
        verified_at=datetime(2026, 9, 10, 12, 0, 0),
        description="Reconciliation finished with discrepancies"
    )
    settlement.reconcile(result)

    await repo.save(settlement)
    await db_session.commit()
    db_session.expire_all()

    fetched = await repo.get_by_id(settlement_id)
    assert fetched.status == SettlementStatus.DISCREPANCY
    assert fetched.reconciliation_state == ReconciliationStatus.DISCREPANCY
    assert fetched.reconciliation_result is not None
    assert fetched.reconciliation_result.status == ReconciliationStatus.DISCREPANCY
    assert len(fetched.reconciliation_result.discrepancies) == 1
    assert fetched.reconciliation_result.discrepancies[0].entry_reference.value == "LINE-ENT1"
    assert fetched.reconciliation_result.discrepancies[0].difference.amount.amount == Decimal("5.00")
    assert fetched.reconciliation_result.discrepancies[0].resolved is False

    # Resolve discrepancy
    fetched.resolve_discrepancy(SettlementLineReference("LINE-ENT1"))
    await repo.save(fetched)
    await db_session.commit()
    db_session.expire_all()

    fetched_resolved = await repo.get_by_id(settlement_id)
    assert fetched_resolved.status == SettlementStatus.RECONCILED
    assert fetched_resolved.reconciliation_state == ReconciliationStatus.MATCHED
    assert fetched_resolved.reconciliation_result.discrepancies[0].resolved is True


@pytest.mark.asyncio
async def test_unique_reference_constraint(repo, base_setup, db_session: AsyncSession):
    # Save first
    settlement1 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value
    await repo.save(settlement1)
    await db_session.commit()

    # Save second with same reference
    settlement2 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=SettlementIdempotencyKey("another-idem-key")
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_SETTLEMENT_REFERENCE"):
        await repo.save(settlement2)
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_unique_idempotency_constraint(repo, base_setup, db_session: AsyncSession):
    # Save first
    settlement1 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value
    await repo.save(settlement1)
    await db_session.commit()

    # Save second with same idempotency key
    settlement2 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=SettlementReference("SET-ANOTHER-REF"),
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_SETTLEMENT_IDEMPOTENCY"):
        await repo.save(settlement2)
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_unique_entry_source_transaction_constraint(repo, base_setup, db_session: AsyncSession):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Entry 1
    entry1 = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("0.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("100.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry1)

    await repo.save(settlement)
    await db_session.commit()

    # Bypass domain specification check by directly appending to entries list
    entry_dup = SettlementEntry(
        id=SettlementLineReference("LINE-ENT2"),
        source_transaction_reference="TX-101", # Duplicate
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("0.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("100.00"),
        currency=base_setup["currency"]
    )
    settlement.entries.append(entry_dup)
    
    with pytest.raises(ValueError, match="DUPLICATE_ENTRY_SOURCE_TRANSACTION"):
        await repo.save(settlement)
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_finalization_protection_on_completed(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("0.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("100.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry)
    settlement.open()
    settlement.start_reconciliation()
    
    # Perfect reconciliation result
    result = ReconciliationResult(
        status=ReconciliationStatus.MATCHED,
        discrepancies=[],
        verified_at=Clock.now(),
        description="Matched"
    )
    settlement.reconcile(result)
    settlement.complete()

    # Save finalized
    await repo.save(settlement)
    await db_session.commit()
    db_session.expire_all()

    # Fetch back
    finalized = await repo.get_by_id(settlement_id)
    assert finalized.status == SettlementStatus.COMPLETED

    # 1. Attempt Mutation
    finalized._props["reference"] = SettlementReference("SET-CHANGED-REF")
    with pytest.raises(ValueError, match="CANNOT_MUTATE_FINALIZED_SETTLEMENT"):
        await repo.save(finalized)
        await db_session.commit()

    # Reset session status
    await db_session.rollback()

    # 2. Attempt Deletion
    with pytest.raises(ValueError, match="CANNOT_DELETE_FINALIZED_SETTLEMENT"):
        await repo.delete(settlement_id)
        await db_session.commit()


@pytest.mark.asyncio
async def test_tenant_isolation(repo, organization: str, db_session: AsyncSession):
    # Org A (test fixture organization)
    org_a = OrganizationId(organization)
    settlement_a = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=org_a,
        reference=SettlementReference("SET-ORG-A-REF"),
        merchant_reference=MerchantReference("MERCH-ORG-A"),
        period=SettlementPeriod(datetime(2026, 9, 1), datetime(2026, 9, 5)),
        currency=SettlementCurrency("USDC"),
        idempotency_key=SettlementIdempotencyKey("idem-org-a")
    ).value

    # Org B (create new org)
    org_b_id = str(uuid4())
    org_b_db = OrganizationDbModel(
        id=org_b_id,
        name="Test Merchant Org B",
        slug=f"test-merchant-org-{org_b_id[:8]}",
        status="ACTIVE",
        default_currency="USDC",
        owner_id=str(uuid4())
    )
    db_session.add(org_b_db)
    await db_session.commit()

    org_b = OrganizationId(org_b_id)
    settlement_b = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=org_b,
        reference=SettlementReference("SET-ORG-B-REF"),
        merchant_reference=MerchantReference("MERCH-ORG-B"),
        period=SettlementPeriod(datetime(2026, 9, 1), datetime(2026, 9, 5)),
        currency=SettlementCurrency("USDC"),
        idempotency_key=SettlementIdempotencyKey("idem-org-b")
    ).value

    await repo.save(settlement_a)
    await repo.save(settlement_b)
    await db_session.commit()
    db_session.expire_all()

    # Query Org A
    list_a = await repo.find_by_organization(org_a)
    assert len(list_a) == 1
    assert list_a[0].reference.value == "SET-ORG-A-REF"

    # Query Org B
    list_b = await repo.find_by_organization(org_b)
    assert len(list_b) == 1
    assert list_b[0].reference.value == "SET-ORG-B-REF"


@pytest.mark.asyncio
async def test_transactional_outbox_and_rollback(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Initial save creates 1 domain event (SettlementCreated)
    await repo.save(settlement)
    await db_session.commit()

    # Assert outbox event exists
    outbox_res = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementCreated")
    )
    events = outbox_res.scalars().all()
    assert len(events) == 1
    assert "merchant_reference" in events[0].payload

    # Now let's trigger a rollback scenario: Add entry and apply adjustment, 
    # but trigger duplicate reference integrity failure on save.
    settlement_new = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"], # Duplicate Reference
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=SettlementIdempotencyKey("new-unique-idem")
    ).value

    # Since it is a duplicate reference, saving this should fail and roll back outbox events too
    with pytest.raises(ValueError, match="DUPLICATE_SETTLEMENT_REFERENCE"):
        async with db_session.begin_nested(): # nested transaction to allow rollback testing in function scoped transaction
            await repo.save(settlement_new)
            
    # Check outbox events count - it should still be 1 (the original SettlementCreated)
    outbox_res_after = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementCreated")
    )
    events_after = outbox_res_after.scalars().all()
    assert len(events_after) == 1


@pytest.mark.asyncio
async def test_pessimistic_locking(repo, base_setup, db_session: AsyncSession):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    await repo.save(settlement)
    await db_session.commit()
    db_session.expire_all()

    # Load with lock=True should execute successfully
    locked_settlement = await repo.get_by_id(settlement_id, lock=True)
    assert locked_settlement is not None
    assert locked_settlement.id.value == settlement_id.value
