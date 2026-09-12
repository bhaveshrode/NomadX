import pytest
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, update

from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import SettlementDbModel, OutboxEventDbModel
from nomadx.src.infrastructure.database.repositories import PostgresPaymentRepository, PostgresSettlementRepository
from nomadx.src.domain.payment import PaymentId, PaymentAsset, PaymentNetwork, Payment
from nomadx.src.domain.settlement.settlement import Settlement, SettlementStatus
from nomadx.src.domain.settlement.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementMethod,
    SettlementSource,
    SettlementIdempotencyKey
)
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.infrastructure.settlement.fake import FakeSettlementExecutionAdapter
from nomadx.src.infrastructure.workers.settlement_worker import SettlementWorker


async def _setup_confirmed_payment(org_id: str, payment_id: str, amount: Decimal = Decimal("5000.00")) -> None:
    async with session_manager.session() as session:
        payment_repo = PostgresPaymentRepository(session)
        currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
        observed_amount = Money(amount, currency)
        asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
        network = PaymentNetwork("BASE", "BLOCKCHAIN")

        payment = Payment.create(
            id=PaymentId(payment_id),
            organization_id=OrganizationId(org_id),
            payment_intent_id=None,
            observed_amount=observed_amount,
            asset=asset,
            network=network,
            transaction_reference=f"0xTx_{payment_id[:8]}",
            sender="0xSenderWallet",
            receiver="0xReceiverWallet",
            observed_at=Clock.now()
        ).value

        payment.confirm()
        await payment_repo.save(payment)
        await session.commit()


async def _create_processing_settlement(
    org_id: str, payment_id: str, settlement_id: str, amount: Decimal, reference: str, idempotency_key: str
) -> None:
    async with session_manager.session() as session:
        settlement_repo = PostgresSettlementRepository(session)
        
        currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
        money = Money(amount, currency)
        asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
        network = SettlementNetwork("BASE")
        destination = SettlementDestination("BLOCKCHAIN_ADDRESS", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", network)
        source = SettlementSource("PAYMENT", payment_id)

        settlement = Settlement.create(
            id=SettlementId(settlement_id),
            organization_id=OrganizationId(org_id),
            reference=SettlementReference(reference),
            asset=asset,
            network=network,
            amount=money,
            destination=destination,
            method=SettlementMethod.DIGITAL_ASSET,
            source=source,
            idempotency_key=SettlementIdempotencyKey(idempotency_key)
        ).value

        settlement.start_processing()
        await settlement_repo.save(settlement)
        await session.commit()


@pytest.mark.asyncio
async def test_settlement_worker_successful_execution():
    adapter = FakeSettlementExecutionAdapter()
    worker = SettlementWorker(adapter)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    idem_key = "idem-worker-success"

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_processing_settlement(org_id, pay_id, set_id, Decimal("1500.00"), "SET-WORK-1", idem_key)

    # Run worker
    count = await worker.process_pending_settlements()
    assert count == 1

    # Verify state in DB is SUBMITTED and transaction hash is saved
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.SUBMITTED
        assert s.transaction_reference == f"0xFakeTx_{set_id[:8]}"
        assert s.submitted_at is not None

        # Verify outbox event generated
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementSubmitted")
        outbox_events = (await session.execute(stmt)).scalars().all()
        assert len(outbox_events) == 1


@pytest.mark.asyncio
async def test_settlement_worker_retryable_failure():
    adapter = FakeSettlementExecutionAdapter()
    worker = SettlementWorker(adapter)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    idem_key = "idem-worker-retryable"
    adapter.set_behavior(idem_key, "RETRYABLE_FAILURE")

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_processing_settlement(org_id, pay_id, set_id, Decimal("1500.00"), "SET-WORK-2", idem_key)

    # Run worker
    count = await worker.process_pending_settlements()
    assert count == 1

    # Verify state in DB is FAILED and failure is retryable
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.FAILED
        assert s.failure.code.value == "NETWORK_UNAVAILABLE"
        assert s.failure.retryable is True

        # Verify outbox event generated
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementFailed")
        outbox_events = (await session.execute(stmt)).scalars().all()
        assert len(outbox_events) == 1


@pytest.mark.asyncio
async def test_settlement_worker_permanent_failure():
    adapter = FakeSettlementExecutionAdapter()
    worker = SettlementWorker(adapter)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    idem_key = "idem-worker-permanent"
    adapter.set_behavior(idem_key, "PERMANENT_FAILURE")

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_processing_settlement(org_id, pay_id, set_id, Decimal("1500.00"), "SET-WORK-3", idem_key)

    # Run worker
    count = await worker.process_pending_settlements()
    assert count == 1

    # Verify state in DB is FAILED and failure is permanent
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.FAILED
        assert s.failure.code.value == "DESTINATION_INVALID"
        assert s.failure.retryable is False


@pytest.mark.asyncio
async def test_settlement_worker_crash_recovery():
    adapter = FakeSettlementExecutionAdapter()
    worker = SettlementWorker(adapter)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    idem_key = "idem-worker-crash"

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_processing_settlement(org_id, pay_id, set_id, Decimal("1500.00"), "SET-WORK-4", idem_key)

    # Simulate crash recovery: call adapter.execute() directly first to simulate successful external submit
    from nomadx.src.infrastructure.settlement.models import SettlementExecutionRequest
    req = SettlementExecutionRequest(
        settlement_id=set_id,
        amount=Decimal("1500.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key=idem_key
    )
    first_res = await adapter.execute(req)
    assert first_res.is_success is True

    # Now run worker (database still says PROCESSING, simulating recovery after crash)
    count = await worker.process_pending_settlements()
    assert count == 1

    # Verify database has recovered the EXACT same transaction reference
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.SUBMITTED
        assert s.transaction_reference == first_res.transaction_reference


@pytest.mark.asyncio
async def test_settlement_worker_tenant_isolation():
    adapter = FakeSettlementExecutionAdapter()
    worker = SettlementWorker(adapter)

    org_a = str(uuid4())
    org_b = str(uuid4())
    pay_a = str(uuid4())
    pay_b = str(uuid4())
    set_a = str(uuid4())
    set_b = str(uuid4())

    await _setup_confirmed_payment(org_a, pay_a)
    await _setup_confirmed_payment(org_b, pay_b)

    await _create_processing_settlement(org_a, pay_a, set_a, Decimal("1000.00"), "SET-WORK-A", "idem-tenant-a")
    await _create_processing_settlement(org_b, pay_b, set_b, Decimal("2000.00"), "SET-WORK-B", "idem-tenant-b")

    # Run worker to process both
    count = await worker.process_pending_settlements()
    assert count == 2

    # Verify both settlements processed correctly for their respective organizations
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s_a = await repo.get_by_id(SettlementId(set_a))
        s_b = await repo.get_by_id(SettlementId(set_b))

        assert s_a.organization_id.value == org_a
        assert s_a.status == SettlementStatus.SUBMITTED

        assert s_b.organization_id.value == org_b
        assert s_b.status == SettlementStatus.SUBMITTED
