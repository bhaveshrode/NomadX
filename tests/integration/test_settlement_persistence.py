import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    UserId,
    OrganizationSettings
)
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.domain.settlement import (
    Settlement,
    SettlementStatus,
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementDestinationType,
    SettlementMethod,
    SettlementSource,
    SettlementSourceType,
    SettlementFailureCode,
    SettlementFailure,
    SettlementIdempotencyKey
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresSettlementRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, SettlementDbModel


async def _setup_organization(session: AsyncSession, org_id_str: str) -> Organization:
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName(f"Org-{org_id_str[:8]}"),
        slug=OrganizationSlug(f"org-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings("USDC")
    ).value
    await org_repo.save(org)
    await session.flush()
    return org


@pytest.mark.asyncio
async def test_settlement_persistence_lifecycle(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    settlement_id = SettlementId(str(uuid4()))
    reference = SettlementReference("SET-2026-000001")
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    network = SettlementNetwork("BASE")
    amount = Money(Decimal("5000.123456"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    destination = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idempotency_key = SettlementIdempotencyKey("idem-lifecycle")

    # 1. Create PENDING
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=OrganizationId(org_id),
        reference=reference,
        asset=asset,
        network=network,
        amount=amount,
        destination=destination,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idempotency_key
    ).value

    repo = PostgresSettlementRepository(db_session)
    await repo.save(settlement)
    await db_session.flush()

    # Clear session cache
    db_session.expunge_all()

    # 2. Retrieve & Reconstruct
    retrieved = await repo.get_by_id(settlement_id)
    assert retrieved is not None
    assert retrieved.id.value == settlement_id.value
    assert retrieved.organization_id.value == org_id
    assert retrieved.reference.value == "SET-2026-000001"
    assert retrieved.amount.amount == Decimal("5000.123456")
    assert retrieved.status == SettlementStatus.PENDING

    # 3. Mutate: PENDING -> PROCESSING
    retrieved.start_processing()
    await repo.save(retrieved)
    await db_session.flush()
    db_session.expunge_all()

    retrieved2 = await repo.get_by_id(settlement_id)
    assert retrieved2.status == SettlementStatus.PROCESSING

    # 4. Mutate: PROCESSING -> SUBMITTED
    retrieved2.submit("0xTxReferenceHash")
    await repo.save(retrieved2)
    await db_session.flush()
    db_session.expunge_all()

    retrieved3 = await repo.get_by_id(settlement_id)
    assert retrieved3.status == SettlementStatus.SUBMITTED
    assert retrieved3.transaction_reference == "0xTxReferenceHash"


@pytest.mark.asyncio
async def test_settlement_failure_and_retry_persistence(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    settlement_id = SettlementId(str(uuid4()))
    reference = SettlementReference("SET-2026-000002")
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    network = SettlementNetwork("BASE")
    amount = Money(Decimal("250.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    destination = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idempotency_key = SettlementIdempotencyKey("idem-failure")

    settlement = Settlement.create(
        id=settlement_id,
        organization_id=OrganizationId(org_id),
        reference=reference,
        asset=asset,
        network=network,
        amount=amount,
        destination=destination,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idempotency_key
    ).value

    repo = PostgresSettlementRepository(db_session)
    settlement.start_processing()
    await repo.save(settlement)
    await db_session.flush()

    # Fail
    failure = SettlementFailure(SettlementFailureCode.NETWORK_UNAVAILABLE, "RPC server crashed", retryable=True)
    settlement.fail(failure)
    await repo.save(settlement)
    await db_session.flush()

    db_session.expunge_all()

    # Verify failure persisted
    retrieved = await repo.get_by_id(settlement_id)
    assert retrieved.status == SettlementStatus.FAILED
    assert retrieved.failure is not None
    assert retrieved.failure.code == SettlementFailureCode.NETWORK_UNAVAILABLE
    assert retrieved.failure.message == "RPC server crashed"
    assert retrieved.failure.retryable is True

    # Retry
    retrieved.retry()
    await repo.save(retrieved)
    await db_session.flush()
    db_session.expunge_all()

    # Verify retrying persisted
    retrieved2 = await repo.get_by_id(settlement_id)
    assert retrieved2.status == SettlementStatus.RETRYING


@pytest.mark.asyncio
async def test_settlement_idempotency_constraints(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    await _setup_organization(db_session, org_a)
    await _setup_organization(db_session, org_b)

    repo = PostgresSettlementRepository(db_session)
    network = SettlementNetwork("BASE")
    dest = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    amount = Money(Decimal("100.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idem_key = SettlementIdempotencyKey("shared-idem-key")

    # 1. Create settlement for Org A
    settlement1 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_a),
        reference=SettlementReference("SET-ORG-A"),
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idem_key
    ).value
    await repo.save(settlement1)
    await db_session.flush()

    # 2. Duplicate key in SAME organization should be rejected
    settlement2 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_a),
        reference=SettlementReference("SET-ORG-A-DUP"),
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idem_key
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_SETTLEMENT_IDEMPOTENCY"):
        await repo.save(settlement2)
        await db_session.flush()

    await db_session.rollback()

    # 3. Duplicate key in DIFFERENT organization should be allowed
    settlement3 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_b),
        reference=SettlementReference("SET-ORG-B"),
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idem_key
    ).value

    await repo.save(settlement3)
    await db_session.flush()


@pytest.mark.asyncio
async def test_settlement_reference_uniqueness(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    repo = PostgresSettlementRepository(db_session)
    network = SettlementNetwork("BASE")
    dest = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    amount = Money(Decimal("100.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    shared_ref = SettlementReference("SET-SHARED-REF")

    # 1. Create first settlement
    settlement1 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=shared_ref,
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=SettlementIdempotencyKey("idem-ref1")
    ).value
    await repo.save(settlement1)
    await db_session.flush()

    # 2. Duplicate reference should fail unique reference validation
    settlement2 = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=shared_ref,
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=SettlementIdempotencyKey("idem-ref2")
    ).value

    with pytest.raises(ValueError, match="DUPLICATE_SETTLEMENT_REFERENCE"):
        await repo.save(settlement2)
        await db_session.flush()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_settlement_tenant_isolation(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    await _setup_organization(db_session, org_a)
    await _setup_organization(db_session, org_b)

    repo = PostgresSettlementRepository(db_session)
    network = SettlementNetwork("BASE")
    dest = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    amount = Money(Decimal("100.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))

    # Create Org B settlement
    settlement_b = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_b),
        reference=SettlementReference("SET-ORG-B-ISOL"),
        asset=asset,
        network=network,
        amount=amount,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=SettlementIdempotencyKey("idem-isol-b")
    ).value
    await repo.save(settlement_b)
    await db_session.flush()

    # Find list for Org A should be empty
    org_a_settlements = await repo.find_by_organization(OrganizationId(org_a))
    assert len(org_a_settlements) == 0

    # Find list for Org B should contain settlement
    org_b_settlements = await repo.find_by_organization(OrganizationId(org_b))
    assert len(org_b_settlements) == 1
    assert org_b_settlements[0].reference.value == "SET-ORG-B-ISOL"


@pytest.mark.asyncio
async def test_settlement_row_level_locking(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    settlement_id = SettlementId(str(uuid4()))
    reference = SettlementReference("SET-2026-000003")
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    network = SettlementNetwork("BASE")
    amount = Money(Decimal("10.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    destination = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idempotency_key = SettlementIdempotencyKey("idem-lock")

    settlement = Settlement.create(
        id=settlement_id,
        organization_id=OrganizationId(org_id),
        reference=reference,
        asset=asset,
        network=network,
        amount=amount,
        destination=destination,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idempotency_key
    ).value

    repo = PostgresSettlementRepository(db_session)
    await repo.save(settlement)
    await db_session.flush()

    # Test lock=True acquires without crashing
    locked = await repo.get_by_id(settlement_id, lock=True)
    assert locked is not None


@pytest.mark.asyncio
async def test_settlement_outbox_transactional_atomicity(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    settlement_id = SettlementId(str(uuid4()))
    reference = SettlementReference("SET-2026-000004")
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    network = SettlementNetwork("BASE")
    amount = Money(Decimal("1500.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    destination = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idempotency_key = SettlementIdempotencyKey("idem-outbox")

    settlement = Settlement.create(
        id=settlement_id,
        organization_id=OrganizationId(org_id),
        reference=reference,
        asset=asset,
        network=network,
        amount=amount,
        destination=destination,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idempotency_key
    ).value

    repo = PostgresSettlementRepository(db_session)
    await repo.save(settlement)
    await db_session.flush()

    # Query outbox events table
    stmt = select(OutboxEventDbModel).where(
        OutboxEventDbModel.event_type == "SettlementCreated"
    )
    res = await db_session.execute(stmt)
    outbox_record = res.scalar_one_or_none()
    assert outbox_record is not None
    assert "SettlementCreated" in outbox_record.event_type


@pytest.mark.asyncio
async def test_settlement_deletion_restriction(db_session: AsyncSession):
    org_id = str(uuid4())
    await _setup_organization(db_session, org_id)

    settlement_id = SettlementId(str(uuid4()))
    reference = SettlementReference("SET-2026-000005")
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    network = SettlementNetwork("BASE")
    amount = Money(Decimal("150.00"), Currency("USDC", CurrencyType.DIGITAL_ASSET))
    destination = SettlementDestination(
        SettlementDestinationType.BLOCKCHAIN_ADDRESS,
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        network
    )
    source = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idempotency_key = SettlementIdempotencyKey("idem-deletion")

    settlement = Settlement.create(
        id=settlement_id,
        organization_id=OrganizationId(org_id),
        reference=reference,
        asset=asset,
        network=network,
        amount=amount,
        destination=destination,
        method=SettlementMethod.DIGITAL_ASSET,
        source=source,
        idempotency_key=idempotency_key
    ).value

    repo = PostgresSettlementRepository(db_session)
    await repo.save(settlement)
    await db_session.flush()

    # Verify physical delete is blocked
    with pytest.raises(ValueError, match="Deleting a financial Settlement record is rejected"):
        await repo.delete(settlement_id)
