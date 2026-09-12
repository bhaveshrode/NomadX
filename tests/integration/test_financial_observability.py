import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    InvoiceDbModel,
    ReceivableDbModel,
    OutboxEventDbModel,
    FinancialConsistencyViolationDbModel
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
            billing_name="Observability Customer",
            billing_email="customer-obs@example.com",
            billing_address="123 Observability St",
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


async def _seed_receivable_db(org_id: str, rec_id: str, inv_id: str, original_amount: Decimal, due_date: datetime) -> None:
    async with session_manager.session() as session:
        rec = ReceivableDbModel(
            id=rec_id,
            organization_id=org_id,
            customer_id=str(uuid4()),
            invoice_id=inv_id,
            reference=f"AR-{uuid4().hex[:6].upper()}",
            customer_reference="CUST-OBS-1",
            invoice_reference="INV-OBS-1",
            currency="USDC",
            original_amount=str(original_amount),
            allocated_amount="0.00",
            outstanding_amount=str(original_amount),
            due_date=due_date,
            status="OPEN",
            aging_bucket="CURRENT",
            dunning_stage="INITIAL",
            idempotency_key=f"idem-{uuid4().hex[:8]}",
            paid_amount="0.00",
            payment_status="UNPAID",
            collection_status="CURRENT",
            created_at=datetime.utcnow() - timedelta(minutes=5),
            updated_at=datetime.utcnow() - timedelta(minutes=5)
        )
        session.add(rec)
        await session.commit()


@pytest.mark.asyncio
async def test_financial_observability_lifecycle_and_isolation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register & Login Tenant A
        email_a = f"owner_obs_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A Observability",
            "organization_slug": f"org-obs-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        org_a = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # 2. Register & Login Tenant B
        email_b = f"owner_obs_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B Observability",
            "organization_slug": f"org-obs-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # --- SEED INCONSISTENT STATE FOR ORG A ---
        inv_id = str(uuid4())
        rec_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        
        # Seed Invoice of $1000.00
        await _seed_invoice_db(org_a, inv_id, Decimal("1000.00"), due_date)
        # Seed Receivable of $900.00 (triggers INVOICE_RECEIVABLE_MISMATCH)
        await _seed_receivable_db(org_a, rec_id, inv_id, Decimal("900.00"), due_date)

        # Seed Domain Event in Outbox for the timeline
        event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="InvoiceIssued",
            payload=json.dumps({
                "invoice_id": inv_id,
                "organization_id": org_a,
                "occurred_on": (datetime.utcnow() - timedelta(minutes=10)).isoformat()
            }),
            processed=True,
            status="COMPLETED",
            created_at=datetime.utcnow() - timedelta(minutes=10)
        )
        async with session_manager.session() as session:
            session.add(event)
            await session.commit()

        # Run Check API for Org A to detect and persist violation
        res_check = await ac.get("/api/financial-consistency/check", headers=headers_a)
        assert res_check.status_code == 200
        report = res_check.json()
        assert report["status"] == "DEGRADED"
        assert report["summary"]["total_violations"] == 1
        assert report["violations"][0]["violation_type"] == "INVOICE_RECEIVABLE_MISMATCH"

        # Query Health Endpoint
        res_health = await ac.get("/api/financial-consistency/health", headers=headers_a)
        assert res_health.status_code == 200
        health = res_health.json()
        assert health["status"] == "DEGRADED"
        assert health["active_violations_count"] == 1

        # Query Active Violations Endpoint
        res_viol = await ac.get("/api/financial-consistency/violations", headers=headers_a)
        assert res_viol.status_code == 200
        viols = res_viol.json()
        assert len(viols) == 1
        assert viols[0]["violation_type"] == "INVOICE_RECEIVABLE_MISMATCH"
        assert viols[0]["resolved"] is False
        assert viols[0]["severity"] == "CRITICAL"
        assert viols[0]["classification"] == "INVOICE_INTEGRITY"

        # Query Metrics
        res_metrics = await ac.get("/api/financial-consistency/metrics", headers=headers_a)
        assert res_metrics.status_code == 200
        metrics = res_metrics.json()
        assert metrics["status"] == "DEGRADED"
        assert metrics["active_violations"] == 1
        assert metrics["resolved_violations"] == 0

        # Query Entity Timeline
        res_time = await ac.get(f"/api/financial-consistency/timeline/{inv_id}", headers=headers_a)
        assert res_time.status_code == 200
        timeline = res_time.json()
        # Should have DOMAIN_EVENT (InvoiceIssued) and CONSISTENCY_VIOLATION_DETECTED
        assert len(timeline) >= 2
        types = [t["type"] for t in timeline]
        assert "DOMAIN_EVENT" in types
        assert "CONSISTENCY_VIOLATION_DETECTED" in types

        # --- MULTI-TENANT ISOLATION BOUNDARY CHECKS ---
        # Query active violations from Org B's headers -> should be empty
        res_viol_b = await ac.get("/api/financial-consistency/violations", headers=headers_b)
        assert res_viol_b.status_code == 200
        assert len(res_viol_b.json()) == 0

        # Query history from Org B -> should be empty
        res_hist_b = await ac.get("/api/financial-consistency/history", headers=headers_b)
        assert res_hist_b.status_code == 200
        assert len(res_hist_b.json()) == 0

        # Query Org A's entity timeline from Org B -> should be empty
        res_time_b = await ac.get(f"/api/financial-consistency/timeline/{inv_id}", headers=headers_b)
        assert res_time_b.status_code == 200
        assert len(res_time_b.json()) == 0

        # --- AUTO-RESOLUTION DETECTION ---
        # Resolve the inconsistency by updating the receivable original_amount to match invoice grand_total ($1000)
        async with session_manager.session() as session:
            stmt_upd = (
                update(ReceivableDbModel)
                .where(ReceivableDbModel.id == rec_id)
                .values(original_amount="1000.00", outstanding_amount="1000.00")
            )
            await session.execute(stmt_upd)
            await session.commit()

        # Run Check again
        res_check_after = await ac.get("/api/financial-consistency/check", headers=headers_a)
        assert res_check_after.status_code == 200
        report_after = res_check_after.json()
        assert report_after["status"] == "HEALTHY"
        assert report_after["summary"]["total_violations"] == 0

        # Query Active Violations -> should be empty now
        res_viol_after = await ac.get("/api/financial-consistency/violations", headers=headers_a)
        assert len(res_viol_after.json()) == 0

        # Query Violation History -> should contain the resolved violation
        res_hist_after = await ac.get("/api/financial-consistency/history", headers=headers_a)
        assert res_hist_after.status_code == 200
        history = res_hist_after.json()
        assert len(history) == 1
        assert history[0]["violation_type"] == "INVOICE_RECEIVABLE_MISMATCH"
        assert history[0]["resolved"] is True
        assert history[0]["resolved_at"] is not None

        # Query Metrics after resolution
        res_metrics_after = await ac.get("/api/financial-consistency/metrics", headers=headers_a)
        metrics_after = res_metrics_after.json()
        assert metrics_after["status"] == "HEALTHY"
        assert metrics_after["active_violations"] == 0
        assert metrics_after["resolved_violations"] == 1
        assert float(metrics_after["health_score"]) == 100.0

        # Query Timeline -> should now include the resolution event
        res_time_after = await ac.get(f"/api/financial-consistency/timeline/{inv_id}", headers=headers_a)
        timeline_after = res_time_after.json()
        types_after = [t["type"] for t in timeline_after]
        assert "CONSISTENCY_VIOLATION_RESOLVED" in types_after
