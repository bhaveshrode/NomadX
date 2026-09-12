import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_concurrent_remediations_for_same_anomaly():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_rconc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner RConc",
            "organization_name": "Org RConc Test",
            "organization_slug": f"org-rconc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed failures to trigger WORKER_FAILURE_BURST
        corr_id = f"rconc-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="RConc stuck worker",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # Detect anomaly
        await ac.post("/api/observability/anomalies/detect", headers=headers)

        # Trigger concurrent remediation sweeps via the HTTP endpoint (which ensures separate DB sessions/locks)
        results = await asyncio.gather(
            ac.post("/api/observability/remediations/run", headers=headers),
            ac.post("/api/observability/remediations/run", headers=headers),
            ac.post("/api/observability/remediations/run", headers=headers),
            ac.post("/api/observability/remediations/run", headers=headers),
            ac.post("/api/observability/remediations/run", headers=headers)
        )

        for res in results:
            assert res.status_code == 200

        # Sum of executed remediations should be exactly 1 because the lock and strategy engine deduplicate them
        executed_counts = [res.json().get("executed_remediations_count", 0) for res in results]
        assert sum(executed_counts) == 1
