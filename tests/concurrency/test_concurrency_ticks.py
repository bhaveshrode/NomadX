import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_concurrent_intelligence_ticks():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_tconc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner TConc",
            "organization_name": "Org TConc Test",
            "organization_slug": f"org-tconc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Set policy to LEVEL_0_OBSERVATIONAL so that remediation does not clear anomaly status on first run
        await ac.post("/api/observability/intelligence/policy", json={
            "readiness_level": "LEVEL_0_OBSERVATIONAL",
            "emergency_disable": False
        }, headers=headers)

        # Seed failures to trigger WORKER_FAILURE_BURST
        corr_id = f"tconc-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="TConc stuck worker",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # Fire 5 concurrent ticks
        results = await asyncio.gather(
            ac.post("/api/observability/intelligence/tick", headers=headers),
            ac.post("/api/observability/intelligence/tick", headers=headers),
            ac.post("/api/observability/intelligence/tick", headers=headers),
            ac.post("/api/observability/intelligence/tick", headers=headers),
            ac.post("/api/observability/intelligence/tick", headers=headers)
        )

        for res in results:
            assert res.status_code == 200

        # Assert no duplicate anomalies are created (exactly 1)
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) == 1

        # Assert exactly 1 active situation exists
        situations = (await ac.get("/api/observability/situations", headers=headers)).json()
        assert len(situations) == 1
