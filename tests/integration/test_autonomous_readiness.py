import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
import json

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_autonomous_readiness_and_intelligence_tick():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email = f"owner_ar_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner AR",
            "organization_name": "Org Autonomous Readiness",
            "organization_slug": f"org-ar-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Register Tenant B
        email_b = f"owner_ar_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B AR",
            "organization_slug": f"org-ar-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # 3. Verify Tenant A default readiness policy
        res_policy = await ac.get("/api/observability/intelligence/policy", headers=headers)
        if res_policy.status_code != 200:
            print("POLICY FAILED:", res_policy.status_code, res_policy.text)
        assert res_policy.status_code == 200
        policy = res_policy.json()
        assert policy["readiness_level"] == "LEVEL_2_CONTROLLED_AUTOMATION"
        assert policy["emergency_disable"] is False

        # 4. Update Policy (Emergency Disable)
        res_update = await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_2_CONTROLLED_AUTOMATION",
            "emergency_disable": True
        }, headers=headers)
        assert res_update.status_code == 200
        assert res_update.json()["emergency_disable"] is True

        # Check Audit Trail log for policy update
        res_audit = await ac.get("/api/observability/control-plane/audit-trail", headers=headers)
        assert res_audit.status_code == 200
        audits = res_audit.json()
        assert len(audits) >= 1
        assert audits[0]["action"] == "UPDATE_READINESS_POLICY"

        # Seed Telemetry failure burst
        corr_id = f"ar-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Telemetry event failure for AR",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # Trigger Closed-Loop tick while Emergency Disable is active
        res_tick = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick.status_code == 200
        tick_res = res_tick.json()
        assert tick_res["remediations_executed"] == 0 # Remediation loop skipped

        # 5. Update Policy (LEVEL_0_OBSERVATIONAL)
        await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_0_OBSERVATIONAL",
            "emergency_disable": False
        }, headers=headers)

        # Trigger closed-loop tick
        res_tick_obs = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick_obs.status_code == 200
        
        # Verify Remediation Action exists but is marked SKIPPED_OBSERVATIONAL
        rems = (await ac.get("/api/observability/remediations", headers=headers)).json()
        assert len(rems) > 0
        assert rems[0]["status"] == "SKIPPED_OBSERVATIONAL"

        # 6. Update Policy (LEVEL_1_ASSISTED)
        await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_1_ASSISTED",
            "emergency_disable": False
        }, headers=headers)

        # Clear anomalies status back so they trigger remediation loop again
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        for a in anoms:
            await ac.post(f"/api/observability/anomalies/{a['id']}/assign", json={"owner_id": "op-test"}, headers=headers)

        # Trigger tick
        res_tick_ast = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick_ast.status_code == 200
        
        # Check newly created remediation status
        rems_updated = (await ac.get("/api/observability/remediations", headers=headers)).json()
        staged_rems = [r for r in rems_updated if r["status"] == "PENDING_APPROVAL"]
        assert len(staged_rems) > 0

        # 7. Tenant Isolation
        res_policy_b = await ac.get("/api/observability/intelligence/policy", headers=headers_b)
        assert res_policy_b.json()["readiness_level"] == "LEVEL_2_CONTROLLED_AUTOMATION"
        assert res_policy_b.json()["emergency_disable"] is False
