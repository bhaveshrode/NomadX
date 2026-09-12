import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
import json


from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_control_plane_and_audit_trail_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email = f"owner_cp_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner CP",
            "organization_name": "Org Control Plane",
            "organization_slug": f"org-cp-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Register Tenant B
        email_b = f"owner_cp_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B CP",
            "organization_slug": f"org-cp-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # 3. Seed failures for Tenant A
        corr_id = f"cp-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker telemetry for CP",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # 4. Trigger anomaly detection & situation evaluation
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        await ac.post("/api/observability/situations/evaluate", headers=headers)

        # Get the anomaly ID
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) == 1
        anom_id = anoms[0]["id"]

        # Get situation ID
        situations = (await ac.get("/api/observability/situations", headers=headers)).json()
        assert len(situations) == 1
        sit_id = situations[0]["id"]

        # 5. Check Control Plane Overview
        res_overview = await ac.get("/api/observability/control-plane/overview", headers=headers)
        assert res_overview.status_code == 200
        overview = res_overview.json()
        assert overview["platform_health"] == "DEGRADED"
        assert overview["active_situations_count"] == 1
        assert overview["active_exceptions_count"] == 1
        assert "ar_event_processor" in overview["degraded_components"]

        # 6. Execute sensitive admin actions (Assignment & Mitigation)
        # Assign
        res_assign = await ac.post(f"/api/observability/anomalies/{anom_id}/assign", json={"owner_id": "operator-1"}, headers=headers)
        assert res_assign.status_code == 200

        # Mitigate
        res_mitigate = await ac.post(f"/api/observability/anomalies/{anom_id}/mitigate", json={"notes": "Completed SLA mitigation steps"}, headers=headers)
        assert res_mitigate.status_code == 200

        # 7. Check Audit Trail logs
        res_audit = await ac.get("/api/observability/control-plane/audit-trail", headers=headers)
        assert res_audit.status_code == 200
        audits = res_audit.json()
        assert len(audits) >= 2
        
        # Verify action details are captured
        actions = [a["action"] for a in audits]
        assert "ASSIGN_EXCEPTION" in actions
        assert "MITIGATE_EXCEPTION" in actions
        
        user_id = reg.json()["user"]["id"]
        assert audits[0]["actor_id"] == user_id

        # 8. Check Situation Timeline trace
        res_timeline = await ac.get(f"/api/observability/control-plane/situations/{sit_id}/timeline", headers=headers)
        assert res_timeline.status_code == 200
        timeline = res_timeline.json()
        assert len(timeline) >= 2 # Should contain telemetry and anomaly events
        event_types = [ev["type"] for ev in timeline]
        assert "telemetry" in event_types
        assert "anomaly" in event_types

        # 9. Tenant Isolation Checks
        # Query overview from Tenant B -> Clean state
        res_overview_b = await ac.get("/api/observability/control-plane/overview", headers=headers_b)
        overview_b = res_overview_b.json()
        assert overview_b["platform_health"] == "HEALTHY"
        assert overview_b["active_situations_count"] == 0
        assert len(overview_b["degraded_components"]) == 0

        # Query Tenant A situation timeline using Tenant B's credentials -> 404
        res_timeline_b = await ac.get(f"/api/observability/control-plane/situations/{sit_id}/timeline", headers=headers_b)
        assert res_timeline_b.status_code == 404

        # Query Tenant B audit trails -> Empty list
        res_audit_b = await ac.get("/api/observability/control-plane/audit-trail", headers=headers_b)
        assert len(res_audit_b.json()) == 0
