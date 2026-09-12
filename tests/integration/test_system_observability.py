import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.domain.organization.value_objects import OrganizationId


@pytest.mark.asyncio
async def test_system_observability_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
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

        # 2. Register Tenant B
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

        # --- SEED TELEMETRY EVENTS ---
        corr_123 = str(uuid4())
        corr_456 = str(uuid4())

        async with container.telemetry_service() as telemetry_service:
            # Event 1: API Request
            await telemetry_service.record_event(
                component="API",
                operation="InvoiceCreation",
                message="Created invoice INV-100 successfully",
                severity="INFO",
                status="SUCCESS",
                duration_ms=120,
                organization_id=org_a,
                correlation_id=corr_123
            )

            # Event 2: Worker execution linked to corr_123
            await telemetry_service.record_event(
                component="WORKER",
                operation="process_payment_events",
                message="Processed payment events for invoice INV-100",
                severity="INFO",
                status="SUCCESS",
                duration_ms=50,
                organization_id=org_a,
                correlation_id=corr_123
            )

            # Event 3: Failed Worker execution linked to corr_456
            await telemetry_service.record_event(
                component="WORKER",
                operation="reconcile_settlement",
                message="Database connection timed out",
                severity="ERROR",
                status="FAILED",
                duration_ms=350,
                organization_id=org_a,
                correlation_id=corr_456
            )

        # --- TEST 1: QUERY TRACES ---
        res_traces = await ac.get("/api/observability/traces", headers=headers_a)
        assert res_traces.status_code == 200
        traces = res_traces.json()
        assert len(traces) == 3

        # Test component filter
        res_filtered = await ac.get("/api/observability/traces?component=API", headers=headers_a)
        assert res_filtered.status_code == 200
        filtered = res_filtered.json()
        assert len(filtered) == 1
        assert filtered[0]["operation"] == "InvoiceCreation"

        # --- TEST 2: GET TRACE BY CORRELATION ID ---
        res_corr = await ac.get(f"/api/observability/traces/{corr_123}", headers=headers_a)
        assert res_corr.status_code == 200
        corr_traces = res_corr.json()
        assert len(corr_traces) == 2
        # Check order is chronological (InvoiceCreation before process_payment_events)
        assert corr_traces[0]["component"] == "API"
        assert corr_traces[1]["component"] == "WORKER"

        # --- TEST 3: COMPONENT HEALTH DASHBOARD ---
        res_health = await ac.get("/api/observability/components", headers=headers_a)
        assert res_health.status_code == 200
        health = res_health.json()
        assert health["api"]["status"] == "HEALTHY"
        assert health["ar_event_processor"]["status"] == "HEALTHY"
        assert health["settlement_automation_worker"]["status"] == "DEGRADED"

        # --- TEST 4: PERFORMANCE & ERROR METRICS ---
        res_metrics = await ac.get("/api/observability/metrics", headers=headers_a)
        assert res_metrics.status_code == 200
        metrics = res_metrics.json()
        assert metrics["total_operations_24h"] == 3
        assert metrics["error_count_24h"] == 1
        assert metrics["error_rate_percent"] == 33.33
        assert metrics["avg_duration_ms"] == 173.33 # (120 + 50 + 350) / 3

        # --- TEST 5: TENANT ISOLATION BOUNDARY ---
        # Query Tenant A's correlation ID traces using Tenant B's headers -> should be empty
        res_corr_b = await ac.get(f"/api/observability/traces/{corr_123}", headers=headers_b)
        assert res_corr_b.status_code == 200
        assert len(res_corr_b.json()) == 0

        # Query metrics for Tenant B -> should be all zeroes
        res_metrics_b = await ac.get("/api/observability/metrics", headers=headers_b)
        assert res_metrics_b.status_code == 200
        metrics_b = res_metrics_b.json()
        assert metrics_b["total_operations_24h"] == 0
