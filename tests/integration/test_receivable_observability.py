import pytest
import json
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from nomadx.apps.api.main import app
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    ReceivableDbModel,
    ReceivableCollectionActionDbModel,
    OutboxEventDbModel,
    InvoiceDbModel,
    ReceivableAllocationDbModel
)
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId


class ControllableClock(Clock):
    _time = datetime(2026, 8, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        if cls._time.tzinfo is None:
            cls._time = cls._time.replace(tzinfo=timezone.utc)
        return cls._time

    @classmethod
    def set_time(cls, new_time: datetime):
        if new_time.tzinfo is None:
            new_time = new_time.replace(tzinfo=timezone.utc)
        cls._time = new_time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


async def _create_test_receivable(session: AsyncSession, org_id: str, rec_id: str, invoice_id: str, status: str = "OPEN") -> None:
    # Setup parent invoice
    inv = InvoiceDbModel(
        id=invoice_id,
        organization_id=org_id,
        invoice_number=f"INV-{invoice_id[:8]}",
        origin_type="NATIVE",
        grand_total="1000.00",
        currency_code="USDC",
        currency_type="DIGITAL_ASSET",
        subtotal="1000.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        payment_terms_type="NET_30",
        issued_at=Clock.now().replace(tzinfo=None) - timedelta(days=5),
        status="ISSUED"
    )
    session.add(inv)

    rec = ReceivableDbModel(
        id=rec_id,
        organization_id=org_id,
        customer_id=str(uuid4()),
        invoice_id=invoice_id,
        reference=f"AR-REC-{rec_id[:8]}",
        customer_reference="CUST-1",
        invoice_reference=f"INV-{invoice_id[:8]}",
        currency="USDC",
        original_amount="1000.00",
        allocated_amount="0.00",
        outstanding_amount="1000.00",
        due_date=Clock.now().replace(tzinfo=None) + timedelta(days=25),
        status=status,
        aging_bucket="CURRENT",
        dunning_stage="NONE",
        dunning_attempt_count=0,
        next_dunning_at=None,
        idempotency_key=f"key-{rec_id}",
        dunning_paused=False,
        paid_amount="0.00",
        payment_status="UNPAID",
        collection_status="CURRENT",
        created_at=Clock.now().replace(tzinfo=None) - timedelta(days=5),
        updated_at=Clock.now().replace(tzinfo=None) - timedelta(days=5)
    )
    session.add(rec)
    await session.commit()


@pytest.mark.asyncio
async def test_ar_observability_endpoints(db_session: AsyncSession):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Register Org A (Owner)
        email_a = f"owner_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Org A Owner",
            "organization_name": "Org A Corp",
            "organization_slug": f"org-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        assert reg_a.status_code == 201
        
        login_a = await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })
        token_a = login_a.json()["token"]
        auth_a = {"Authorization": f"Bearer {token_a}"}
        ctx_a = (await ac.get("/api/secure-context", headers=auth_a)).json()
        org_a = ctx_a["organization_id"]

        # 2. Register Org B (Owner)
        email_b = f"owner_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Org B Owner",
            "organization_name": "Org B Corp",
            "organization_slug": f"org-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        assert reg_b.status_code == 201

        login_b = await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })
        token_b = login_b.json()["token"]
        auth_b = {"Authorization": f"Bearer {token_b}"}
        ctx_b = (await ac.get("/api/secure-context", headers=auth_b)).json()
        org_b = ctx_b["organization_id"]

        # 3. Create Receivable under Org A
        rec_id_a = str(uuid4())
        inv_id_a = str(uuid4())
        await _create_test_receivable(db_session, org_a, rec_id_a, inv_id_a)

        # 4. Verify Health check is initially HEALTHY
        health_resp = await ac.get("/api/receivables/health", headers=auth_a)
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "HEALTHY"
        assert health_resp.json()["database"] == "UP"

        # 5. Populate outbox events & collection actions in DB under Org A to trigger metrics
        action_id = str(uuid4())
        async with session_manager.session() as session:
            # Add outbox events for audit log check
            ev1 = OutboxEventDbModel(
                id=str(uuid4()),
                event_type="ReceivableCreated",
                payload=json.dumps({
                    "receivable_id": rec_id_a,
                    "invoice_id": inv_id_a,
                    "organization_id": org_a,
                    "_metadata": {
                        "correlation_id": "corr-123",
                        "causation_id": "caus-456",
                        "actor": "system_worker"
                    }
                }),
                processed=True,
                processed_at=Clock.now().replace(tzinfo=None),
                status="COMPLETED",
                created_at=Clock.now().replace(tzinfo=None) - timedelta(hours=1)
            )
            session.add(ev1)

            # Add completed collection action for latency and throughput metrics
            act1 = ReceivableCollectionActionDbModel(
                id=action_id,
                organization_id=org_a,
                receivable_id=rec_id_a,
                action_type="SEND_REMINDER",
                scheduled_at=Clock.now().replace(tzinfo=None) - timedelta(hours=2),
                status="COMPLETED",
                attempt_count=1,
                idempotency_key="key-act-1",
                created_at=Clock.now().replace(tzinfo=None) - timedelta(hours=2),
                processed_at=Clock.now().replace(tzinfo=None) - timedelta(hours=1)
            )
            session.add(act1)
            await session.commit()

        # 6. Verify Metrics API
        metrics_resp = await ac.get("/api/receivables/metrics", headers=auth_a)
        assert metrics_resp.status_code == 200
        metrics = metrics_resp.json()
        assert metrics["total_receivables_count"] == 1
        assert metrics["collection_actions_by_status"]["COMPLETED"] == 1
        assert metrics["average_collection_latency_seconds"] == 3600.0 # 2 hours to 1 hour difference
        assert metrics["worker_throughput_completed_24h"] == 1

        # 7. Verify Audit log API
        audit_resp = await ac.get("/api/receivables/audit", headers=auth_a)
        assert audit_resp.status_code == 200
        audit = audit_resp.json()
        assert len(audit) >= 1
        assert audit[0]["event_type"] == "ReceivableCreated"
        assert audit[0]["correlation_id"] == "corr-123"
        assert audit[0]["causation_id"] == "caus-456"
        assert audit[0]["actor"] == "system_worker"

        # 8. Verify Receivable Timeline API
        timeline_resp = await ac.get(f"/api/receivables/{rec_id_a}/timeline", headers=auth_a)
        assert timeline_resp.status_code == 200
        timeline = timeline_resp.json()
        assert len(timeline) >= 3 # Creation, Action Scheduled, Action Completed
        types = [t["event_type"] for t in timeline]
        assert "CREATION" in types
        assert "COLLECTION_ACTION_SCHEDULED" in types
        assert "COLLECTION_ACTION_COMPLETED" in types

        # 9. Verify Collection History API
        history_resp = await ac.get(f"/api/receivables/{rec_id_a}/collection-history", headers=auth_a)
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert len(history) == 1
        assert history[0]["id"] == action_id
        assert history[0]["status"] == "COMPLETED"

        # 10. Verify Cross-Tenant Isolation
        # Org B should not see Org A's receivable timeline (404)
        timeline_b = await ac.get(f"/api/receivables/{rec_id_a}/timeline", headers=auth_b)
        assert timeline_b.status_code == 404

        # Org B should not see Org A's collection history (404)
        history_b = await ac.get(f"/api/receivables/{rec_id_a}/collection-history", headers=auth_b)
        assert history_b.status_code == 404

        # Org B's metrics should not include Org A's data (total_receivables_count == 0)
        metrics_b = await ac.get("/api/receivables/metrics", headers=auth_b)
        assert metrics_b.status_code == 200
        assert metrics_b.json()["total_receivables_count"] == 0

        # Org B's health diagnostic should not include Org A's stuck jobs
        health_b = await ac.get("/api/receivables/health", headers=auth_b)
        assert health_b.status_code == 200
        assert health_b.json()["status"] == "HEALTHY"

        # Org B's audit logs should be empty
        audit_b = await ac.get("/api/receivables/audit", headers=auth_b)
        assert audit_b.status_code == 200
        assert len(audit_b.json()) == 0

        # 11. Verify Health check status becomes DEGRADED when a stuck job is introduced
        async with session_manager.session() as session:
            stuck_act = ReceivableCollectionActionDbModel(
                id=str(uuid4()),
                organization_id=org_a,
                receivable_id=rec_id_a,
                action_type="SEND_REMINDER",
                scheduled_at=Clock.now().replace(tzinfo=None) - timedelta(minutes=30),
                status="PROCESSING",
                attempt_count=1,
                idempotency_key="key-act-stuck",
                created_at=Clock.now().replace(tzinfo=None) - timedelta(minutes=30)
            )
            session.add(stuck_act)
            await session.commit()

        health_resp2 = await ac.get("/api/receivables/health", headers=auth_a)
        assert health_resp2.status_code == 200
        assert health_resp2.json()["status"] == "DEGRADED"
        assert health_resp2.json()["stuck_collection_actions"] == 1
