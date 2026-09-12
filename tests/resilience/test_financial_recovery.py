import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    OutboxEventDbModel,
    SettlementReconciliationDbModel,
    FinancialRemediationAuditDbModel,
    InvoiceDbModel
)


async def _seed_invoice_db(org_id: str, inv_id: str, grand_total: Decimal, due_date: datetime) -> None:
    async with session_manager.session() as session:
        cust_id = str(uuid4())
        invoice_model = InvoiceDbModel(
            id=inv_id,
            organization_id=org_id,
            invoice_number=f"INV-{str(uuid4())[:8]}",
            status="ISSUED",
            origin_type="NATIVE",
            source_system="NomadX",
            external_invoice_id=None,
            customer_id=cust_id,
            billing_name="Recovery Customer",
            billing_email="customer-rec@example.com",
            billing_address="123 Recovery St",
            billing_country="US",
            billing_tax_identifier=None,
            currency_code="USDC",
            currency_type="DIGITAL_ASSET",
            subtotal=str(grand_total),
            discount_total="0.00",
            charge_total="0.00",
            tax_total="0.00",
            grand_total=str(grand_total),
            payment_terms_type="DUE_ON_RECEIPT",
            due_at=due_date,
            issued_at=datetime.utcnow() - timedelta(minutes=10),
            created_at=datetime.utcnow() - timedelta(minutes=10)
        )
        session.add(invoice_model)
        await session.commit()


@pytest.mark.asyncio
async def test_financial_recovery_remediation_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email_a = f"owner_rec_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A Recovery",
            "organization_slug": f"org-rec-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        org_a = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # 2. Register Tenant B
        email_b = f"owner_rec_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B Recovery",
            "organization_slug": f"org-rec-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # --- TEST 1: RETRY FAILED OUTBOX EVENT ---
        inv_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        await _seed_invoice_db(org_a, inv_id, Decimal("1000.00"), due_date)

        event_id = str(uuid4())
        # Seed a failed event for Org A
        event = OutboxEventDbModel(
            id=event_id,
            event_type="InvoiceIssued",
            payload=json.dumps({
                "invoice_id": inv_id,
                "organization_id": org_a,
                "occurred_on": datetime.utcnow().isoformat()
            }),
            processed=False,
            status="DEAD_LETTERED",
            attempts=3,
            created_at=datetime.utcnow() - timedelta(minutes=5)
        )
        async with session_manager.session() as session:
            session.add(event)
            await session.commit()

        # Trigger Retry via API
        res_retry = await ac.post(f"/api/financial-recovery/retry-event/{event_id}", headers=headers_a)
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "SUCCESS"

        # Verify Event status in DB is COMPLETED (or processed by AREventProcessor)
        async with session_manager.session() as session:
            res_db = await session.execute(select(OutboxEventDbModel).where(OutboxEventDbModel.id == event_id))
            ev_db = res_db.scalar_one_or_none()
            assert ev_db.status == "COMPLETED"
            assert ev_db.processed is True
            assert ev_db.attempts == 0

        # Verify Remediation Audit record
        res_audit = await ac.get("/api/financial-recovery/remediations", headers=headers_a)
        assert res_audit.status_code == 200
        audits = res_audit.json()
        assert len(audits) >= 1
        assert audits[0]["action_type"] == "RETRY_EVENT"
        assert audits[0]["status"] == "SUCCESS"
        assert audits[0]["target_entity_id"] == event_id

        # --- TEST 2: EVENT REPLAY & FINALIZATION SAFETY GUARD ---
        # Seed a completed settlement batch
        settlement_id = str(uuid4())
        async with session_manager.session() as session:
            batch = SettlementReconciliationDbModel(
                id=settlement_id,
                organization_id=org_a,
                reference="SET-COMP-1",
                merchant_reference="MERCH-C",
                period_start=datetime.utcnow(),
                period_end=datetime.utcnow(),
                currency_code="USDC",
                status="COMPLETED", # Finalized!
                reconciliation_state="RECONCILED",
                idempotency_key="idem-comp-1",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(batch)
            await session.commit()

        # Seed a completed event replaying which would mutate this settlement
        replay_event_id = str(uuid4())
        replay_event = OutboxEventDbModel(
            id=replay_event_id,
            event_type="SettlementCompleted",
            payload=json.dumps({
                "settlement_id": settlement_id,
                "organization_id": org_a,
                "occurred_on": datetime.utcnow().isoformat()
            }),
            processed=True,
            status="COMPLETED",
            attempts=1,
            created_at=datetime.utcnow() - timedelta(minutes=5)
        )
        async with session_manager.session() as session:
            session.add(replay_event)
            await session.commit()

        # Replay event -> should be blocked by safety checks due to finalization lock
        res_replay = await ac.post(f"/api/financial-recovery/replay-event/{replay_event_id}", headers=headers_a)
        assert res_replay.status_code == 400
        assert "Settlement is already finalized and completed" in res_replay.json()["detail"]

        # Verify audit log shows ESCALATED
        res_audit2 = await ac.get("/api/financial-recovery/remediations", headers=headers_a)
        audits2 = res_audit2.json()
        assert audits2[0]["action_type"] == "REPLAY_EVENT"
        assert audits2[0]["status"] == "ESCALATED"
        assert "blocked" in audits2[0]["error_message"]

        # --- TEST 3: STUCK PROCESSING RECOVERY ---
        stuck_event_id = str(uuid4())
        stuck_event = OutboxEventDbModel(
            id=stuck_event_id,
            event_type="InvoiceIssued",
            payload=json.dumps({
                "invoice_id": str(uuid4()),
                "organization_id": org_a,
                "occurred_on": datetime.utcnow().isoformat()
            }),
            processed=False,
            status="PROCESSING", # stuck!
            attempts=1,
            created_at=datetime.utcnow() - timedelta(minutes=20)
        )
        async with session_manager.session() as session:
            session.add(stuck_event)
            await session.commit()

        # Trigger Stuck Recovery
        res_stuck = await ac.post("/api/financial-recovery/recover-stuck", headers=headers_a)
        assert res_stuck.status_code == 200
        assert res_stuck.json()["events_recovered"] >= 1

        # Verify event returned to RETRYING / pending state
        async with session_manager.session() as session:
            res_db2 = await session.execute(select(OutboxEventDbModel).where(OutboxEventDbModel.id == stuck_event_id))
            ev_db2 = res_db2.scalar_one_or_none()
            assert ev_db2.status == "RETRYING"

        # --- TEST 4: TENANT ISOLATION BOUNDARY ---
        # Try to retry Org A's event using Org B's auth headers -> should fail with 404 to prevent metadata leakage
        res_retry_b = await ac.post(f"/api/financial-recovery/retry-event/{event_id}", headers=headers_b)
        assert res_retry_b.status_code == 404
        assert "not found" in res_retry_b.json()["detail"].lower()

        # Query history for Org B -> should remain completely empty of Org A's actions
        res_audit_b = await ac.get("/api/financial-recovery/remediations", headers=headers_b)
        assert len(res_audit_b.json()) == 0
