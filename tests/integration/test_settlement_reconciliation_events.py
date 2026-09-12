import pytest
import json
from uuid import uuid4
from datetime import datetime
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, SettlementReconciliationDbModel, SettlementEntryDbModel
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_event_driven_settlement_entry_creation():
    org_id = str(uuid4())
    tx_ref = f"0xTX-{uuid4().hex[:8].upper()}"
    corr_id = str(uuid4())

    # 1. Insert a PaymentReceived outbox event
    payload = {
        "organization_id": org_id,
        "payment_id": "pay-101",
        "amount": "250.00",
        "currency": "USDC",
        "transaction_reference": tx_ref,
        "observed_at": "2026-09-02T12:00:00Z",
        "_metadata": {
            "correlation_id": corr_id
        }
    }

    async with session_manager.session() as session:
        event_id = str(uuid4())
        event_model = OutboxEventDbModel(
            id=event_id,
            event_type="PaymentReceived",
            payload=json.dumps(payload),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        session.add(event_model)
        await session.commit()

    # 2. Run the Settlement Event Processor
    async with container.settlement_event_processor() as processor:
        count = await processor.process_financial_events()
        assert count == 1

    # 3. Verify event was marked processed and transaction lineage tracked
    async with session_manager.session() as session:
        evt_model = (await session.execute(
            select(OutboxEventDbModel).where(OutboxEventDbModel.payload.like(f"%{tx_ref}%"))
        )).scalar_one()
        assert evt_model.processed
        assert evt_model.status == "COMPLETED"

        # 4. Verify draft settlement batch was auto-created for Sept 2026
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == org_id,
                SettlementReconciliationDbModel.currency_code == "USDC"
            )
        )
        res = await session.execute(stmt)
        settlement = res.scalar_one_or_none()
        assert settlement is not None
        assert settlement.status == "OPEN"
        assert settlement.period_start == datetime(2026, 9, 1, 0, 0)

        # 5. Verify entry was automatically added with correct amount and reference
        entry_stmt = (
            select(SettlementEntryDbModel)
            .where(SettlementEntryDbModel.settlement_id == settlement.id)
        )
        entry = (await session.execute(entry_stmt)).scalar_one()
        assert entry.source_transaction_reference == tx_ref
        assert entry.gross_amount == "250.00"
        assert entry.net_amount == "250.00"

    # 6. Idempotency protection check: dispatch a duplicate event with same transaction_reference
    async with session_manager.session() as session:
        dup_event_id = str(uuid4())
        dup_model = OutboxEventDbModel(
            id=dup_event_id,
            event_type="PaymentReceived",
            payload=json.dumps(payload),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        session.add(dup_model)
        await session.commit()

    async with container.settlement_event_processor() as processor:
        dup_count = await processor.process_financial_events()
        assert dup_count == 1 # still processes the outbox event

    # Verify no duplicate entry was added in the settlement
    async with session_manager.session() as session:
        entries = (await session.execute(
            select(SettlementEntryDbModel).where(SettlementEntryDbModel.settlement_id == settlement.id)
        )).scalars().all()
        assert len(entries) == 1 # still exactly one entry!
