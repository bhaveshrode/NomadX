import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container


@pytest.mark.asyncio
async def test_state_machine_transitions_failures():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_sm_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner SM",
            "organization_name": "Org SM Test",
            "organization_slug": f"org-sm-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed failures to get anomaly
        corr_id = f"sm-incident-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker trace",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id,
                    organization_id=org_id
                )

        # Detect
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) > 0
        anomaly_id = anoms[0]["id"]
        assert anoms[0]["status"] == "DETECTED"

        # 1. Test invalid mitigation transition before assignment (Mitigate is allowed from DETECTED, but let's resolve first)
        # Directly update status to RESOLVED via update_anomaly_status endpoint (or resolve transition)
        # Wait, let's test that manually attempting to update anomaly status to RESOLVED directly from DETECTED works.
        # But after status becomes RESOLVED, we cannot mitigate or assign anymore!
        res_resolve = await ac.post(
            f"/api/observability/anomalies/{anomaly_id}/status",
            json={"status": "RESOLVED", "notes": "Resolved directly"},
            headers=headers
        )
        assert res_resolve.status_code == 200

        # Now try to Assign owner to this RESOLVED anomaly -> Should fail with 500 or 400 because database query service raises ValueError.
        res_assign = await ac.post(
            f"/api/observability/anomalies/{anomaly_id}/assign",
            json={"owner_id": "operator-1"},
            headers=headers
        )
        assert res_assign.status_code == 500 or res_assign.status_code == 400

        # Now try to Mitigate this RESOLVED anomaly -> Should fail with 500 or 400
        res_mitigate = await ac.post(
            f"/api/observability/anomalies/{anomaly_id}/mitigate",
            json={"notes": "Belated mitigation"},
            headers=headers
        )
        assert res_mitigate.status_code == 500 or res_mitigate.status_code == 400

        # 2. Test invalid remediation retries
        # Let's seed another failure and tick the loop to get remediation actions
        corr_id_2 = f"sm-incident-2-{uuid4().hex[:6]}"
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker trace 2",
                    severity="ERROR",
                    status="FAILED",
                    correlation_id=corr_id_2,
                    organization_id=org_id
                )

        await ac.post("/api/observability/intelligence/tick", headers=headers)
        remediations = (await ac.get("/api/observability/remediations", headers=headers)).json()
        assert len(remediations) > 0
        rem_id = remediations[0]["id"]
        
        # Remediation completed or failed. Manual retry of remediations in EXECUTING or SUCCESS status must fail.
        # If it is in SUCCESS status, query service retry_remediation will raise ValueError.
        # Let's manually change its status to SUCCESS in the database or test the API response.
        # The endpoint POST /api/observability/remediations/{remediation_id}/retry
        # If the status is not FAILED/VERIFICATION_FAILED, it raises ValueError.
        res_retry = await ac.post(
            f"/api/observability/remediations/{rem_id}/retry",
            headers=headers
        )
        # Since it succeeded or verified, retry should fail
        assert res_retry.status_code == 500 or res_retry.status_code == 400
