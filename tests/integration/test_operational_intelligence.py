import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from datetime import datetime, timezone, timedelta
import json

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    AnomalyDbModel,
    OperationalSituationDbModel
)


@pytest.mark.asyncio
async def test_operational_intelligence_situations_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email = f"owner_intel_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Intel",
            "organization_name": "Org Intelligence",
            "organization_slug": f"org-intel-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Register Tenant B
        email_b = f"owner_intel_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B Intelligence",
            "organization_slug": f"org-intel-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # 3. Seed correlated telemetry logs
        # Correlation ID linking them is "sla-incident-123"
        corr_id = "sla-incident-123"
        async with container.telemetry_service() as telemetry_service:
            # High API latency
            await telemetry_service.record_event(
                component="API",
                operation="POST /payments/allocate",
                message="Slow allocate API request",
                severity="WARNING",
                status="SUCCESS",
                duration_ms=800.0,
                correlation_id=corr_id,
                organization_id=org_id
            )
            # Worker failures
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker exception",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # 4. Trigger anomaly detection scan
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) >= 2 # HIGH_LATENCY and WORKER_FAILURE_BURST

        # 5. Evaluate Operational Situations
        res_eval = await ac.post("/api/observability/situations/evaluate", headers=headers)
        assert res_eval.status_code == 200
        assert res_eval.json()["evaluated_situations_count"] == 1
        assert res_eval.json()["situations"][0]["action"] == "CREATED"

        # 6. Query active situations list
        res_list = await ac.get("/api/observability/situations", headers=headers)
        assert res_list.status_code == 200
        situations = res_list.json()
        assert len(situations) == 1
        assert situations[0]["status"] == "ACTIVE"
        assert situations[0]["correlation_id"] == corr_id
        # Score is high due to multiple anomalies and readiness block
        assert situations[0]["impact_score"] >= 50.0

        # 7. Check situation detail & causality chain
        sit_id = situations[0]["id"]
        res_detail = await ac.get(f"/api/observability/situations/{sit_id}", headers=headers)
        assert res_detail.status_code == 200
        detail = res_detail.json()
        
        # Verify components & causality chain logs
        assert "api" in detail["evidence"]["affected_components"]
        assert "ar_event_processor" in detail["evidence"]["affected_components"]
        
        causality = detail["evidence"]["causality_chain"]
        assert len(causality) == 1
        assert causality[0]["upstream"] == "api"
        assert causality[0]["downstream"] == "workers"

        # 8. Test Resolving Situations
        # Mark the related anomalies resolved
        for a in anoms:
            await ac.post(f"/api/observability/anomalies/{a['id']}/status", json={"status": "RESOLVED", "notes": "Resolved"}, headers=headers)
        
        # Re-evaluate -> situation becomes resolved
        res_eval2 = await ac.post("/api/observability/situations/evaluate", headers=headers)
        assert res_eval2.json()["evaluated_situations_count"] == 0
        
        res_detail2 = await ac.get(f"/api/observability/situations/{sit_id}", headers=headers)
        assert res_detail2.json()["status"] == "RESOLVED"

        # 9. Tenant Boundary Checks
        # Query from tenant B -> empty list
        res_list_b = await ac.get("/api/observability/situations", headers=headers_b)
        assert len(res_list_b.json()) == 0
        
        # Query Tenant A situation using B context -> 404
        res_detail_b = await ac.get(f"/api/observability/situations/{sit_id}", headers=headers_b)
        assert res_detail_b.status_code == 404
