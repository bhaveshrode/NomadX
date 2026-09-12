import pytest
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
from nomadx.src.application.settlement_reconciliation.commands import (
    CreateSettlementCommand,
    OpenSettlementCommand,
    AddSettlementEntryCommand,
    AddSettlementAdjustmentCommand,
    BeginReconciliationCommand,
    ReconcileSettlementCommand,
    RecordSettlementDiscrepancyCommand,
    ResolveSettlementDiscrepancyCommand,
    CompleteSettlementCommand,
    CancelSettlementCommand
)
from nomadx.src.bootstrap.container import container
from nomadx.src.presentation.api.dependencies.context import ExecutionContext
from nomadx.src.infrastructure.database.models import (
    SettlementReconciliationDbModel,
    OutboxEventDbModel,
    OrganizationDbModel
)


@pytest.fixture
async def organization(db_session: AsyncSession) -> str:
    org_id = str(uuid4())
    org = OrganizationDbModel(
        id=org_id,
        name="Test App Merchant Org",
        slug=f"test-app-merchant-org-{org_id[:8]}",
        status="ACTIVE",
        default_currency="USDC",
        owner_id=str(uuid4())
    )
    db_session.add(org)
    await db_session.commit()
    return org_id


@pytest.fixture
def execution_context(organization: str) -> ExecutionContext:
    return ExecutionContext(
        user_id="test_user",
        organization_id=organization,
        role="OWNER"
    )


@pytest.mark.asyncio
async def test_create_settlement_use_case(execution_context, db_session: AsyncSession):
    command = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )

    async with container.create_settlement_reconciliation_handler() as handler:
        res = await handler.handle(command, execution_context)
        assert res.is_success
        settlement = res.value
        assert settlement.status == SettlementStatus.DRAFT

    # Verify persistent state
    db_session.expire_all()
    q_res = await db_session.execute(
        select(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == command.settlement_id)
    )
    db_model = q_res.scalar_one_or_none()
    assert db_model is not None
    assert db_model.reference == command.reference
    assert db_model.status == "DRAFT"

    # Verify event generated in outbox
    outbox_res = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "SettlementCreated")
    )
    events = outbox_res.scalars().all()
    assert len(events) >= 1


@pytest.mark.asyncio
async def test_idempotent_creation(execution_context, db_session: AsyncSession):
    command = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )

    async with container.create_settlement_reconciliation_handler() as handler:
        res1 = await handler.handle(command, execution_context)
        assert res1.is_success

        # Run identical second command
        res2 = await handler.handle(command, execution_context)
        assert res2.is_success
        assert res2.value.id.value == res1.value.id.value


@pytest.mark.asyncio
async def test_tenant_isolation(execution_context):
    org_b_id = str(uuid4())
    command = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_b_id, # Org B
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-B",
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2026, 9, 5),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )

    async with container.create_settlement_reconciliation_handler() as handler:
        # Organization execution mismatch
        res = await handler.handle(command, execution_context)
        assert res.is_failure
        assert res.error.code == "UNAUTHORIZED_ORGANIZATION_ACCESS"


@pytest.mark.asyncio
async def test_settlement_entries_and_adjustments(execution_context, db_session: AsyncSession):
    settlement_id = str(uuid4())
    create_cmd = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )

    async with container.create_settlement_reconciliation_handler() as h_create:
        await h_create.handle(create_cmd, execution_context)

    # 1. Open Settlement
    async with container.open_settlement_reconciliation_handler() as h_open:
        open_res = await h_open.handle(OpenSettlementCommand(settlement_id, execution_context.organization_id), execution_context)
        assert open_res.is_success
        assert open_res.value.status == SettlementStatus.OPEN

    # 2. Add entry
    entry_cmd = AddSettlementEntryCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        entry_reference="LINE-ENT1",
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=Decimal("1000.00"),
        fees_amount=Decimal("15.00"),
        adjustments_amount=Decimal("5.00"),
        net_amount=Decimal("980.00"),
        currency_code="USDC"
    )
    async with container.add_settlement_entry_reconciliation_handler() as h_entry:
        entry_res = await h_entry.handle(entry_cmd, execution_context)
        assert entry_res.is_success
        assert len(entry_res.value.entries) == 1

        # Attempt duplicate entry
        dup_res = await h_entry.handle(entry_cmd, execution_context)
        assert dup_res.is_failure
        assert dup_res.error.code == "DUPLICATE_SETTLEMENT_ENTRY"

        # Attempt currency mismatch entry
        mismatch_cmd = AddSettlementEntryCommand(
            settlement_id=settlement_id,
            organization_id=execution_context.organization_id,
            entry_reference="LINE-ENT2",
            source_transaction_reference="TX-102",
            source_context_type="INVOICE",
            gross_amount=Decimal("1000.00"),
            fees_amount=Decimal("15.00"),
            adjustments_amount=Decimal("5.00"),
            net_amount=Decimal("980.00"),
            currency_code="EUR"
        )
        mismatch_res = await h_entry.handle(mismatch_cmd, execution_context)
        assert mismatch_res.is_failure
        assert mismatch_res.error.code == "SETTLEMENT_CURRENCY_MISMATCH"

    # 3. Add adjustment
    adj_cmd = AddSettlementAdjustmentCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        adjustment_id="adj-01",
        type="PROCESSING_FEE",
        description="Transaction discount",
        amount=Decimal("30.00"),
        currency_code="USDC"
    )
    async with container.add_settlement_adjustment_reconciliation_handler() as h_adj:
        adj_res = await h_adj.handle(adj_cmd, execution_context)
        assert adj_res.is_success
        assert len(adj_res.value.adjustments) == 1
        assert adj_res.value.net_amount.amount == Decimal("950.00") # 980 - 30 = 950.00


@pytest.mark.asyncio
async def test_reconciliation_discrepancies_and_finalization(execution_context, db_session: AsyncSession):
    settlement_id = str(uuid4())
    create_cmd = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )
    
    async with container.create_settlement_reconciliation_handler() as h_create:
        await h_create.handle(create_cmd, execution_context)
    async with container.open_settlement_reconciliation_handler() as h_open:
        await h_open.handle(OpenSettlementCommand(settlement_id, execution_context.organization_id), execution_context)

    # Add entry to make it balance mathematically
    entry_cmd = AddSettlementEntryCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        entry_reference="LINE-ENT1",
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=Decimal("100.00"),
        fees_amount=Decimal("5.00"),
        adjustments_amount=Decimal("0.00"),
        net_amount=Decimal("95.00"),
        currency_code="USDC"
    )
    async with container.add_settlement_entry_reconciliation_handler() as h_entry:
        await h_entry.handle(entry_cmd, execution_context)

    # 1. Begin Reconciliation
    async with container.begin_reconciliation_handler() as h_begin:
        begin_res = await h_begin.handle(BeginReconciliationCommand(settlement_id, execution_context.organization_id), execution_context)
        assert begin_res.is_success
        assert begin_res.value.status == SettlementStatus.RECONCILING

    # 2. Record Discrepancy manually
    disc_cmd = RecordSettlementDiscrepancyCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        entry_reference="LINE-ENT1",
        discrepancy_type="AMOUNT_MISMATCH",
        expected_amount=Decimal("95.00"),
        actual_amount=Decimal("90.00"),
        description="Manual log"
    )
    async with container.record_settlement_discrepancy_handler() as h_disc:
        disc_res = await h_disc.handle(disc_cmd, execution_context)
        assert disc_res.is_success
        assert disc_res.value.status == SettlementStatus.DISCREPANCY
        assert len(disc_res.value.reconciliation_result.discrepancies) == 1

    # 3. Complete (Should Fail due to discrepancy)
    async with container.complete_settlement_reconciliation_handler() as h_complete:
        comp_fail_res = await h_complete.handle(CompleteSettlementCommand(settlement_id, execution_context.organization_id), execution_context)
        assert comp_fail_res.is_failure
        assert comp_fail_res.error.code == "SETTLEMENT_DISCREPANCY_EXISTS"

    # 4. Resolve Discrepancy
    async with container.resolve_settlement_discrepancy_handler() as h_resolve:
        resolve_res = await h_resolve.handle(
            ResolveSettlementDiscrepancyCommand(settlement_id, execution_context.organization_id, "LINE-ENT1"),
            execution_context
        )
        assert resolve_res.is_success
        assert resolve_res.value.status == SettlementStatus.RECONCILED

    # 5. Complete (Should Succeed now)
    async with container.complete_settlement_reconciliation_handler() as h_complete:
        comp_res = await h_complete.handle(CompleteSettlementCommand(settlement_id, execution_context.organization_id), execution_context)
        assert comp_res.is_success
        assert comp_res.value.status == SettlementStatus.COMPLETED

    # 6. Attempt mutation after finalized
    async with container.add_settlement_adjustment_reconciliation_handler() as h_adj:
        adj_fail = await h_adj.handle(
            AddSettlementAdjustmentCommand(
                settlement_id=settlement_id,
                organization_id=execution_context.organization_id,
                adjustment_id="adj-02",
                type="MANUAL_ADJUSTMENT",
                description="Late charge",
                amount=Decimal("5.00"),
                currency_code="USDC"
            ),
            execution_context
        )
        assert adj_fail.is_failure
        assert "finalized" in adj_fail.error.message.lower() or "completed" in adj_fail.error.message.lower()


@pytest.mark.asyncio
async def test_cancellation_flow(execution_context):
    settlement_id = str(uuid4())
    create_cmd = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )
    
    async with container.create_settlement_reconciliation_handler() as h_create:
        await h_create.handle(create_cmd, execution_context)
    async with container.open_settlement_reconciliation_handler() as h_open:
        await h_open.handle(OpenSettlementCommand(settlement_id, execution_context.organization_id), execution_context)

    # Cancel open settlement
    async with container.cancel_settlement_reconciliation_handler() as h_cancel:
        res = await h_cancel.handle(CancelSettlementCommand(settlement_id, execution_context.organization_id), execution_context)
        assert res.is_success
        assert res.value.status == SettlementStatus.CANCELLED


@pytest.mark.asyncio
async def test_invalid_lifecycle_transitions(execution_context):
    settlement_id = str(uuid4())
    create_cmd = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=execution_context.organization_id,
        reference=f"SET-{uuid4().hex[:8].upper()}",
        merchant_reference="MERCH-ORG-A",
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        currency_code="USDC",
        idempotency_key=f"idem-{uuid4().hex[:12]}"
    )

    async with container.create_settlement_reconciliation_handler() as h_create:
        await h_create.handle(create_cmd, execution_context)

    # Try to complete from DRAFT (invalid)
    async with container.complete_settlement_reconciliation_handler() as h_complete:
        res = await h_complete.handle(CompleteSettlementCommand(settlement_id, execution_context.organization_id), execution_context)
        assert res.is_failure
