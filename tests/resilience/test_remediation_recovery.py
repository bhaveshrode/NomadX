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
    RemediationActionDbModel
)


@pytest.mark.asyncio
async def test_remediation_and_recovery_loop_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant
        email = f"owner_rem_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Rem",
            "organization_name": "Org Remediation",
            "organization_slug": f"org-rem-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Seed worker failure burst (triggers WORKER_FAILURE_BURST)
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker failure event",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 3. Detect anomaly
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) == 1
        ex_id = anoms[0]["id"]
        assert anoms[0]["status"] == "DETECTED"

        # 4. Trigger automated remediation scan (attempt 1)
        res_run = await ac.post("/api/observability/remediations/run", headers=headers)
        assert res_run.status_code == 200
        run_data = res_run.json()
        assert run_data["executed_remediations_count"] == 1
        assert run_data["remediations"][0]["action_type"] == "RECOVER_WORKER"
        assert run_data["remediations"][0]["status"] == "SUCCESS"

        # Check anomaly resolved state
        res_anom = (await ac.get(f"/api/observability/anomalies/{ex_id}", headers=headers)).json()
        assert res_anom["status"] == "RESOLVED"
        assert "automated worker recovery" in res_anom["resolution_notes"]

        # Check remediation history query
        res_history = await ac.get("/api/observability/remediations", headers=headers)
        assert res_history.status_code == 200
        history = res_history.json()
        assert len(history) == 1
        assert history[0]["status"] == "SUCCESS"
        assert history[0]["action_type"] == "RECOVER_WORKER"
        rem_id = history[0]["id"]

        # 5. Test retry limit escalation safety
        # Re-trigger anomaly by seeding new failures
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker failure again",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )
        
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        anoms2 = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        active_anom = [a for a in anoms2 if a["status"] == "DETECTED"][0]
        ex2_id = active_anom["id"]

        # Seed 3 failed attempts in the DB to exceed the budget limit of 3
        async with session_manager.session() as session:
            for _ in range(3):
                act = RemediationActionDbModel(
                    id=str(uuid4()),
                    action_type="RECOVER_WORKER",
                    target_component="ar_event_processor",
                    exception_id=ex2_id,
                    trigger_condition="Worker failure burst detected",
                    status="FAILED",
                    attempt_count=1,
                    created_at=datetime.utcnow(),
                    initiating_rule="WORKER_FAILURE_BURST",
                    organization_id=org_id
                )
                session.add(act)
            await session.commit()

        # Run pipeline -> detects attempt count = 4 > 3 -> auto-escalates exception and skips execution
        res_run2 = await ac.post("/api/observability/remediations/run", headers=headers)
        assert res_run2.json()["executed_remediations_count"] == 0

        # Verify anomaly is escalated
        res_anom2 = (await ac.get(f"/api/observability/anomalies/{ex2_id}", headers=headers)).json()
        assert res_anom2["escalated"] is True
        assert res_anom2["priority"] == "CRITICAL"
        assert res_anom2["status"] == "ACKNOWLEDGED"

        # 6. Test manual retry API
        # Retrieve one of the failed remediations
        remediations = (await ac.get("/api/observability/remediations?status=FAILED", headers=headers)).json()
        failed_rem_id = remediations[0]["id"]

        res_retry = await ac.post(f"/api/observability/remediations/{failed_rem_id}/retry", headers=headers)
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "SUCCESS"
        assert "Retry triggered successfully" in res_retry.json()["message"]
