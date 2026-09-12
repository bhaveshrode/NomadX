import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta
import json
from contextlib import asynccontextmanager
from sqlalchemy import select, update
from unittest.mock import MagicMock, AsyncMock

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import SettlementReconciliationDbModel, PaymentDbModel, OutboxEventDbModel
from nomadx.src.bootstrap.container import container
from nomadx.src.domain.settlement_reconciliation.value_objects import SettlementId
from nomadx.src.application.settlement_reconciliation.automation_service import ReconciliationAutomationService
from nomadx.src.infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository


@pytest.mark.asyncio
async def test_settlement_reconciliation_transient_failure_and_backoff():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # Register & login
        reg = await ac.post("/auth/register", json={
            "email": "owner_transient@example.com",
            "password": "strongpassword123",
            "name": "Owner Transient",
            "organization_name": "Org Transient",
            "organization_slug": f"org-trans-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={"email": "owner_transient@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Create Settlement
        ref = f"SET-TRANS-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers)

        # Mock the service provider on container to return a transient failure repo
        original_provider = container.settlement_reconciliation_automation_service

        @asynccontextmanager
        async def mock_provider():
            async with session_manager.session() as session:
                settlement_repo = PostgresSettlementRepository(session)
                mock_source_repo = MagicMock()
                mock_source_repo.find_by_period = AsyncMock(side_effect=Exception("OperationalError: database connection timeout"))
                yield ReconciliationAutomationService(settlement_repo, mock_source_repo)

        container.settlement_reconciliation_automation_service = mock_provider

        try:
            # Reconcile -> must catch transient error, set status to RETRYING, next_retry_at to +1 min
            res_rec = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/reconcile", headers=headers)
            # Reconcile endpoint returns HTTP 500 on execution failure but aggregates state is persisted
            assert res_rec.status_code == 500
        finally:
            container.settlement_reconciliation_automation_service = original_provider

        # Verify state in DB
        async with session_manager.session() as session:
            db_model = (await session.execute(
                select(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == settlement_id)
            )).scalar_one()
            assert db_model.processing_state == "RETRYING"
            assert db_model.attempt_count == 1
            assert db_model.failure_classification == "TRANSIENT"
            assert db_model.next_retry_at is not None
            assert "database connection timeout" in db_model.last_error


@pytest.mark.asyncio
async def test_settlement_reconciliation_permanent_failure():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg = await ac.post("/auth/register", json={
            "email": "owner_perm@example.com",
            "password": "strongpassword123",
            "name": "Owner Perm",
            "organization_name": "Org Perm",
            "organization_slug": f"org-perm-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={"email": "owner_perm@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Create Settlement
        ref = f"SET-PERM-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers)

        # Mock the service provider on container to return a permanent failure repo
        original_provider = container.settlement_reconciliation_automation_service

        @asynccontextmanager
        async def mock_provider():
            async with session_manager.session() as session:
                settlement_repo = PostgresSettlementRepository(session)
                mock_source_repo = MagicMock()
                mock_source_repo.find_by_period = AsyncMock(side_effect=ValueError("Corrupt source transaction fields"))
                yield ReconciliationAutomationService(settlement_repo, mock_source_repo)

        container.settlement_reconciliation_automation_service = mock_provider

        try:
            # Reconcile
            await ac.post(f"/api/settlement-reconciliations/{settlement_id}/reconcile", headers=headers)
        finally:
            container.settlement_reconciliation_automation_service = original_provider

        # Verify state in DB is FAILED directly
        async with session_manager.session() as session:
            db_model = (await session.execute(
                select(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == settlement_id)
            )).scalar_one()
            assert db_model.processing_state == "FAILED"
            assert db_model.failure_classification == "PERMANENT"
            assert db_model.next_retry_at is None
            assert "Corrupt source transaction fields" in db_model.last_error


@pytest.mark.asyncio
async def test_settlement_reconciliation_stuck_job_and_event_recovery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg = await ac.post("/auth/register", json={
            "email": "owner_stuck@example.com",
            "password": "strongpassword123",
            "name": "Owner Stuck",
            "organization_name": "Org Stuck",
            "organization_slug": f"org-stuck-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={"email": "owner_stuck@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Create a Settlement batch and manually force it into "PROCESSING" state with old started_at (crash simulation)
        ref = f"SET-STUCK-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        async with session_manager.session() as session:
            await session.execute(
                update(SettlementReconciliationDbModel)
                .where(SettlementReconciliationDbModel.id == settlement_id)
                .values(
                    processing_state="PROCESSING",
                    processing_started_at=datetime.utcnow() - timedelta(minutes=25)
                )
            )
            await session.commit()

        # 2. Run stuck settlements recovery
        async with container.settlement_reconciliation_automation_service() as service:
            recovered_count = await service.recover_stuck_settlements(timeout_minutes=15)
            assert recovered_count == 1

        # Check DB state -> is now RETRYING
        async with session_manager.session() as session:
            db_model = (await session.execute(
                select(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == settlement_id)
            )).scalar_one()
            assert db_model.processing_state == "RETRYING"
            assert db_model.next_retry_at is not None

        # 3. Create a stuck event and run event recovery
        async with session_manager.session() as session:
            event_id = str(uuid4())
            event_model = OutboxEventDbModel(
                id=event_id,
                event_type="PaymentReceived",
                payload=json.dumps({"organization_id": org_id}),
                processed=False,
                status="PROCESSING",
                created_at=datetime.utcnow() - timedelta(minutes=10)
            )
            session.add(event_model)
            await session.commit()

        async with container.settlement_event_processor() as processor:
            recovered_evts = await processor.recover_stuck_events(timeout_minutes=5)
            assert recovered_evts == 1

        async with session_manager.session() as session:
            evt = (await session.execute(
                select(OutboxEventDbModel).where(OutboxEventDbModel.id == event_id)
            )).scalar_one()
            assert evt.status == "PENDING"
            assert not evt.processed


@pytest.mark.asyncio
async def test_settlement_reconciliation_operational_controls():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg = await ac.post("/auth/register", json={
            "email": "owner_ops@example.com",
            "password": "strongpassword123",
            "name": "Owner Ops",
            "organization_name": "Org Ops",
            "organization_slug": f"org-ops-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={"email": "owner_ops@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Create Settlement
        ref = f"SET-OPS-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        # 1. Test Pause
        res_pause = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/pause", headers=headers)
        assert res_pause.status_code == 200
        assert res_pause.json()["paused"] is True

        # 2. Test Resume
        res_res = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/resume", headers=headers)
        assert res_res.status_code == 200
        assert res_res.json()["paused"] is False

        # 3. Test Replay
        res_replay = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/replay", headers=headers)
        assert res_replay.status_code == 200
        assert res_replay.json()["processing_state"] == "PENDING"
        assert res_replay.json()["attempt_count"] == 0

        # 4. Check timeline, metrics & health endpoints
        res_tl = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/timeline", headers=headers)
        assert res_tl.status_code == 200
        assert isinstance(res_tl.json(), list)

        res_met = await ac.get("/api/settlement-reconciliations/metrics", headers=headers)
        assert res_met.status_code == 200
        assert "total_settlements" in res_met.json()

        res_hl = await ac.get("/api/settlement-reconciliations/health", headers=headers)
        assert res_hl.status_code == 200
        assert "status" in res_hl.json()
        
        # Test audit log trace retrieval
        res_aud = await ac.get("/api/settlement-reconciliations/audit", headers=headers)
        assert res_aud.status_code == 200
        assert isinstance(res_aud.json(), list)


@pytest.mark.asyncio
async def test_settlement_reconciliation_end_to_end_recovery_and_resolution():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg = await ac.post("/auth/register", json={
            "email": "owner_e2e_rec@example.com",
            "password": "strongpassword123",
            "name": "Owner E2E Rec",
            "organization_name": "Org E2E Rec",
            "organization_slug": f"org-e2e-rec-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={"email": "owner_e2e_rec@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Create settlement batch
        ref = f"SET-RECOV-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-E2E",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers)

        # Add entry where NO corresponding payment will be found initially (will trigger MISSING_TRANSACTION discrepancy)
        tx_ref = f"0xTX-{uuid4().hex[:8].upper()}"
        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-E2E-1",
            "source_transaction_reference": tx_ref,
            "source_context_type": "PAYMENT",
            "gross_amount": 1200.00,
            "fees_amount": 0.00,
            "adjustments_amount": 0.00,
            "net_amount": 1200.00,
            "currency_code": "USDC"
        }, headers=headers)

        # 2. Reconcile -> moves to DISCREPANCY status
        res_rec = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/reconcile", headers=headers)
        assert res_rec.json()["status"] == "DISCREPANCY"

        # 3. Simulate a crash where it stays in PROCESSING execution state
        async with session_manager.session() as session:
            await session.execute(
                update(SettlementReconciliationDbModel)
                .where(SettlementReconciliationDbModel.id == settlement_id)
                .values(
                    processing_state="PROCESSING",
                    processing_started_at=datetime.utcnow() - timedelta(minutes=25)
                )
            )
            await session.commit()

        # 4. Trigger recovery
        async with container.settlement_reconciliation_automation_service() as service:
            rec_count = await service.recover_stuck_settlements(timeout_minutes=15)
            assert rec_count == 1

        # 5. Resolve discrepancy manually
        res_res = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/discrepancies/LINE-E2E-1/resolve", headers=headers)
        assert res_res.json()["status"] == "RECONCILED"

        # 6. Complete settlement
        res_comp = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/complete", headers=headers)
        assert res_comp.status_code == 200
        assert res_comp.json()["status"] == "COMPLETED"
        assert res_comp.json()["processing_state"] == "COMPLETED"
