import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_api_idempotency_behavior():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_idem_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Idem",
            "organization_name": "Org Idem Test",
            "organization_slug": f"org-idem-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Double policy update
        res_p1 = await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_0_OBSERVATIONAL",
            "emergency_disable": False
        }, headers=headers)
        assert res_p1.status_code == 200

        res_p2 = await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_0_OBSERVATIONAL",
            "emergency_disable": False
        }, headers=headers)
        assert res_p2.status_code == 200

        # 2. Seed failures
        corr_id = f"idem-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Idem test stuck worker",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # 3. Double intelligence tick execution
        res_tick_1 = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick_1.status_code == 200

        res_tick_2 = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick_2.status_code == 200

        # Verify only one anomaly/situation is present (idempotent correlation)
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) == 1
        anom_id = anoms[0]["id"]

        situations = (await ac.get("/api/observability/situations", headers=headers)).json()
        assert len(situations) == 1

        # 4. Double assignment of same anomaly
        res_assign_1 = await ac.post(f"/api/observability/anomalies/{anom_id}/assign", json={"owner_id": "operator-1"}, headers=headers)
        assert res_assign_1.status_code == 200

        res_assign_2 = await ac.post(f"/api/observability/anomalies/{anom_id}/assign", json={"owner_id": "operator-1"}, headers=headers)
        assert res_assign_2.status_code == 200

        # Double mitigation of same anomaly
        res_mit_1 = await ac.post(f"/api/observability/anomalies/{anom_id}/mitigate", json={"notes": "Resolved successfully"}, headers=headers)
        assert res_mit_1.status_code == 200

        res_mit_2 = await ac.post(f"/api/observability/anomalies/{anom_id}/mitigate", json={"notes": "Resolved successfully"}, headers=headers)
        assert res_mit_2.status_code == 200
