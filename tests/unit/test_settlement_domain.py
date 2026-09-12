import pytest
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.settlement import (
    Settlement,
    SettlementStatus,
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestinationType,
    SettlementDestination,
    SettlementMethod,
    SettlementSourceType,
    SettlementSource,
    SettlementFailureCode,
    SettlementFailure,
    SettlementIdempotencyKey,
    SettlementCreated,
    SettlementProcessingStarted,
    SettlementSubmitted,
    SettlementConfirmed,
    SettlementCompleted,
    SettlementFailed,
    SettlementRetrying,
    SettlementCancelled
)


class ControllableClock(Clock):
    _time = datetime(2026, 9, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        return cls._time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


@pytest.fixture
def test_setup():
    org_id = OrganizationId(str(uuid4()))
    cust_wallet = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    net = SettlementNetwork("BASE")
    dest = SettlementDestination(SettlementDestinationType.BLOCKCHAIN_ADDRESS, cust_wallet, net)
    asset = SettlementAsset("USDC", "DIGITAL_ASSET", 6)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    amount = Money(Decimal("5000.00"), currency)
    ref = SettlementReference("SET-2026-000001")
    src = SettlementSource(SettlementSourceType.PAYMENT, str(uuid4()))
    idem_key = SettlementIdempotencyKey("idem-12345")
    
    return {
        "org_id": org_id,
        "dest": dest,
        "asset": asset,
        "amount": amount,
        "ref": ref,
        "src": src,
        "idem_key": idem_key,
        "net": net,
        "currency": currency
    }


def test_valid_settlement_creation(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    res = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    )
    assert res.is_success
    settlement = res.value
    assert settlement.status == SettlementStatus.PENDING
    assert settlement.amount.amount == Decimal("5000.00")
    assert len(settlement.domain_events) == 1
    assert isinstance(settlement.domain_events[0], SettlementCreated)


def test_positive_amount_required(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    zero_amount = Money(Decimal("0.00"), test_setup["currency"])
    res = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=zero_amount,
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    )
    assert res.is_failure
    assert "positive" in res.error.message


def test_destination_validation(test_setup):
    # Incompatible method and destination: DIGITAL_ASSET method with BANK_ACCOUNT type destination
    bank_dest = SettlementDestination(SettlementDestinationType.BANK_ACCOUNT, "US-12345678", SettlementNetwork("BANK"))
    settlement_id = SettlementId(str(uuid4()))
    res = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=bank_dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    )
    assert res.is_failure
    assert "incompatible" in res.error.message


def test_reference_format_validation():
    with pytest.raises(ValueError, match="must start with 'SET-'"):
        SettlementReference("INVALID-REF-123")


def test_idempotency_key_validation():
    with pytest.raises(ValueError, match="cannot be empty"):
        SettlementIdempotencyKey("")


def test_precision_floats_rejected(test_setup):
    with pytest.raises(TypeError, match="Floating point numbers are not allowed"):
        Money(50.55, test_setup["currency"])


def test_settlement_successful_lifecycle(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value

    settlement.clear_domain_events()

    # 1. PENDING -> PROCESSING
    p_res = settlement.start_processing()
    assert p_res.is_success
    assert settlement.status == SettlementStatus.PROCESSING
    assert len(settlement.domain_events) == 1
    assert isinstance(settlement.domain_events[0], SettlementProcessingStarted)

    # 2. PROCESSING -> SUBMITTED
    s_res = settlement.submit("0xTxHash123")
    assert s_res.is_success
    assert settlement.status == SettlementStatus.SUBMITTED
    assert settlement.transaction_reference == "0xTxHash123"
    assert isinstance(settlement.domain_events[1], SettlementSubmitted)

    # 3. SUBMITTED -> CONFIRMED
    c_res = settlement.confirm()
    assert c_res.is_success
    assert settlement.status == SettlementStatus.CONFIRMED
    assert isinstance(settlement.domain_events[2], SettlementConfirmed)

    # 4. CONFIRMED -> COMPLETED
    comp_res = settlement.complete()
    assert comp_res.is_success
    assert settlement.status == SettlementStatus.COMPLETED
    assert isinstance(settlement.domain_events[3], SettlementCompleted)


def test_invalid_lifecycle_transitions(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value

    # PENDING -> CONFIRMED (invalid)
    res1 = settlement.confirm()
    assert res1.is_failure

    # PENDING -> COMPLETED (invalid)
    res2 = settlement.complete()
    assert res2.is_failure

    # Move to COMPLETED to test terminal states mutations
    settlement.start_processing()
    settlement.submit("0xHash")
    settlement.confirm()
    settlement.complete()

    # COMPLETED -> PROCESSING (invalid)
    res3 = settlement.start_processing()
    assert res3.is_failure

    # COMPLETED -> CANCELLED (invalid)
    res4 = settlement.cancel()
    assert res4.is_failure


def test_cancellation_semantics(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value

    # PENDING -> CANCELLED (valid)
    res_cancel = settlement.cancel()
    assert res_cancel.is_success
    assert settlement.status == SettlementStatus.CANCELLED
    assert isinstance(settlement.domain_events[1], SettlementCancelled)

    # Cancel again (invalid)
    res_cancel_dup = settlement.cancel()
    assert res_cancel_dup.is_failure


def test_submitted_cannot_be_cancelled(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value

    settlement.start_processing()
    settlement.submit("0xHash")
    
    # SUBMITTED -> CANCELLED (invalid)
    res = settlement.cancel()
    assert res.is_failure


def test_retryable_vs_non_retryable_failures(test_setup):
    settlement_id = SettlementId(str(uuid4()))
    settlement = Settlement.create(
        id=settlement_id,
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value

    settlement.start_processing()

    # 1. Non-retryable Failure
    fail_non = SettlementFailure(SettlementFailureCode.DESTINATION_INVALID, "Bad destination address", retryable=False)
    settlement.fail(fail_non)
    assert settlement.status == SettlementStatus.FAILED
    assert settlement.failure.retryable is False

    # Try to retry (invalid)
    retry_res1 = settlement.retry()
    assert retry_res1.is_failure

    # 2. Reset and test Retryable Failure
    settlement_retryable = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=test_setup["org_id"],
        reference=test_setup["ref"],
        asset=test_setup["asset"],
        network=test_setup["net"],
        amount=test_setup["amount"],
        destination=test_setup["dest"],
        method=SettlementMethod.DIGITAL_ASSET,
        source=test_setup["src"],
        idempotency_key=test_setup["idem_key"]
    ).value
    
    settlement_retryable.start_processing()
    fail_retryable = SettlementFailure(SettlementFailureCode.NETWORK_UNAVAILABLE, "RPC rate limit", retryable=True)
    settlement_retryable.fail(fail_retryable)
    assert settlement_retryable.status == SettlementStatus.FAILED
    assert settlement_retryable.failure.retryable is True

    # Try to retry (valid)
    retry_res2 = settlement_retryable.retry()
    assert retry_res2.is_success
    assert settlement_retryable.status == SettlementStatus.RETRYING
    assert isinstance(settlement_retryable.domain_events[3], SettlementRetrying)

    # Transition from RETRYING -> PROCESSING
    p_res2 = settlement_retryable.start_processing()
    assert p_res2.is_success
    assert settlement_retryable.status == SettlementStatus.PROCESSING
