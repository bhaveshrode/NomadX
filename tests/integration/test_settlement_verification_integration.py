import pytest
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select

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
from nomadx.src.infrastructure.settlement.fake import FakeSettlementVerificationAdapter
from nomadx.src.application.settlement.verification import (
    SettlementVerificationService,
    SettlementVerificationEvidence,
    ConfirmationPolicy
)
from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry
from nomadx.src.infrastructure.workers.settlement_verification_worker import SettlementVerificationWorker


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


async def _create_submitted_settlement(
    org_id: str, payment_id: str, settlement_id: str, amount: Decimal, reference: str, tx_ref: str
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
            idempotency_key=SettlementIdempotencyKey(f"idem-{settlement_id[:8]}")
        ).value

        settlement.start_processing()
        settlement.submit(tx_ref)
        await settlement_repo.save(settlement)
        await session.commit()


@pytest.mark.asyncio
async def test_worker_insufficient_confirmations_remains_submitted():
    adapter = FakeSettlementVerificationAdapter()
    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)
    worker = SettlementVerificationWorker(adapter, service)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    tx_ref = "0xLowConfTx"

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_submitted_settlement(org_id, pay_id, set_id, Decimal("1000.00"), "SET-VER-1", tx_ref)

    # Mock evidence with ONLY 1 confirmation (required 3 on BASE)
    evidence = SettlementVerificationEvidence(
        transaction_reference=tx_ref,
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=100,
        confirmations=1
    )
    adapter.set_evidence(tx_ref, evidence)

    # Run worker
    count = await worker.process_submitted_settlements()
    assert count == 0  # Not fully verified (insufficient confirmations)

    # Verify status remains SUBMITTED in DB
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.SUBMITTED


@pytest.mark.asyncio
async def test_worker_successful_verification_completion():
    adapter = FakeSettlementVerificationAdapter()
    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)
    worker = SettlementVerificationWorker(adapter, service)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    tx_ref = "0xHighConfTx"

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_submitted_settlement(org_id, pay_id, set_id, Decimal("1000.00"), "SET-VER-2", tx_ref)

    # Mock evidence with 3 confirmations (sufficient)
    evidence = SettlementVerificationEvidence(
        transaction_reference=tx_ref,
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    adapter.set_evidence(tx_ref, evidence)

    # Run worker
    count = await worker.process_submitted_settlements()
    assert count == 1

    # Verify status is COMPLETED in DB
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.COMPLETED

        # Verify outbox event generated
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementCompleted")
        outbox_events = (await session.execute(stmt)).scalars().all()
        assert len(outbox_events) == 1


@pytest.mark.asyncio
async def test_worker_failed_transaction_transitions_to_failed():
    adapter = FakeSettlementVerificationAdapter()
    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)
    worker = SettlementVerificationWorker(adapter, service)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())
    tx_ref = "0xRevertedTx"

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_submitted_settlement(org_id, pay_id, set_id, Decimal("1000.00"), "SET-VER-3", tx_ref)

    # Mock evidence with transaction status = 0 (reverted)
    evidence = SettlementVerificationEvidence(
        transaction_reference=tx_ref,
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=0,  # reverted
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    adapter.set_evidence(tx_ref, evidence)

    # Run worker
    count = await worker.process_submitted_settlements()
    assert count == 1

    # Verify state in DB is FAILED
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.FAILED
        assert s.failure.code.value == "TRANSACTION_REJECTED"


@pytest.mark.asyncio
async def test_worker_rpc_failure_does_not_corrupt_state():
    class CrashingVerificationAdapter(FakeSettlementVerificationAdapter):
        async def fetch_evidence(self, network: str, transaction_reference: str):
            raise Exception("RPC Timeout Connection error")

    adapter = CrashingVerificationAdapter()
    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)
    worker = SettlementVerificationWorker(adapter, service)

    org_id = str(uuid4())
    pay_id = str(uuid4())
    set_id = str(uuid4())

    await _setup_confirmed_payment(org_id, pay_id)
    await _create_submitted_settlement(org_id, pay_id, set_id, Decimal("1000.00"), "SET-VER-4", "0xRpcCrash")

    # Run worker
    count = await worker.process_submitted_settlements()
    assert count == 0  # skipped safely without crashing the worker process

    # DB state remains SUBMITTED
    async with session_manager.session() as session:
        repo = PostgresSettlementRepository(session)
        s = await repo.get_by_id(SettlementId(set_id))
        assert s.status == SettlementStatus.SUBMITTED
