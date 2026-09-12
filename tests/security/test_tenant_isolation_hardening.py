import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_systematic_tenant_isolation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email_a = f"owner_iso_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Owner Iso A",
            "organization_name": "Org Iso A",
            "organization_slug": f"org-iso-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        org_a = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # 2. Register Tenant B
        email_b = f"owner_iso_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner Iso B",
            "organization_name": "Org Iso B",
            "organization_slug": f"org-iso-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # 3. Seed failure & loop tick for Tenant A
        corr_a = f"iso-a-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Tenant A Stuck Worker",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_a,
                    organization_id=org_a
                )

        await ac.post("/api/observability/intelligence/tick", headers=headers_a)
        
        # Get Tenant A's anomaly & situation
        anoms_a = (await ac.get("/api/observability/anomalies", headers=headers_a)).json()
        assert len(anoms_a) > 0
        anomaly_a_id = anoms_a[0]["id"]

        situations_a = (await ac.get("/api/observability/situations", headers=headers_a)).json()
        assert len(situations_a) > 0
        sit_a_id = situations_a[0]["id"]

        # 4. Tenant B attempts to read/modify Tenant A's anomaly
        res_read = await ac.get(f"/api/observability/anomalies/{anomaly_a_id}", headers=headers_b)
        assert res_read.status_code == 404

        res_assign = await ac.post(f"/api/observability/anomalies/{anomaly_a_id}/assign", json={"owner_id": "attacker"}, headers=headers_b)
        assert res_assign.status_code in [400, 404, 500]

        res_mitigate = await ac.post(f"/api/observability/anomalies/{anomaly_a_id}/mitigate", json={"notes": "unauthorized"}, headers=headers_b)
        assert res_mitigate.status_code in [400, 404, 500]

        # Tenant B attempts to read Tenant A's timeline
        res_timeline = await ac.get(f"/api/observability/control-plane/situations/{sit_a_id}/timeline", headers=headers_b)
        assert res_timeline.status_code == 404

        # 5. Tenant B attempts to manipulate Tenant A's policy
        # Setup Tenant A to LEVEL_1_ASSISTED
        await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_1_ASSISTED",
            "emergency_disable": False
        }, headers=headers_a)

        # Tenant B posts policy change -> should only affect Tenant B
        await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_0_OBSERVATIONAL",
            "emergency_disable": True
        }, headers=headers_b)

        # Assert Tenant A's policy remains unchanged
        policy_a = (await ac.get("/api/observability/intelligence/policy", headers=headers_a)).json()
        assert policy_a["readiness_level"] == "LEVEL_1_ASSISTED"
        assert policy_a["emergency_disable"] is False
