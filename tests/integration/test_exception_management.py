import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from datetime import datetime, timezone, timedelta
import json

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import AnomalyDbModel


@pytest.mark.asyncio
async def test_exception_management_and_workflows_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant
        email = f"owner_ex_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Ex",
            "organization_name": "Org Exceptions",
            "organization_slug": f"org-ex-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Seed 3 failed worker telemetry events (triggers WORKER_FAILURE_BURST)
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Telemetry worker task execution error",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 3. Trigger anomaly detection scan -> Creates exception anomaly
        res_detect = await ac.post("/api/observability/anomalies/detect", headers=headers)
        assert res_detect.status_code == 200
        anoms = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        assert len(anoms) == 1
        a_id = anoms[0]["id"]
        
        # Verify priority and category calculations
        assert anoms[0]["priority"] == "HIGH"
        assert anoms[0]["category"] == "TECHNICAL"
        assert anoms[0]["status"] == "DETECTED"

        # 4. Test Assignment
        res_assign = await ac.post(
            f"/api/observability/anomalies/{a_id}/assign",
            json={"owner_id": "operator-bob"},
            headers=headers
        )
        assert res_assign.status_code == 200
        assert res_assign.json()["current_status"] == "ASSIGNED"
        assert res_assign.json()["owner_id"] == "operator-bob"

        # Check evidence history
        a_detail = (await ac.get(f"/api/observability/anomalies/{a_id}", headers=headers)).json()
        assert "assignment_history" in a_detail["evidence"]
        assert a_detail["evidence"]["assignment_history"][0]["new_owner"] == "operator-bob"

        # 5. Test Mitigation
        res_mitigate = await ac.post(
            f"/api/observability/anomalies/{a_id}/mitigate",
            json={"notes": "Throttled background execution"},
            headers=headers
        )
        assert res_mitigate.status_code == 200
        assert res_mitigate.json()["current_status"] == "MITIGATED"
        assert res_mitigate.json()["mitigation_notes"] == "Throttled background execution"

        # 6. Test Manual Escalation
        res_escalate = await ac.post(
            f"/api/observability/anomalies/{a_id}/escalate",
            json={"reason": "Customer SLA affected"},
            headers=headers
        )
        assert res_escalate.status_code == 200
        assert res_escalate.json()["escalated"] is True
        assert res_escalate.json()["priority"] == "CRITICAL"

        # 7. Test Recurrence Auto-Escalation
        # Resolve the first one
        await ac.post(f"/api/observability/anomalies/{a_id}/status", json={"status": "RESOLVED", "notes": "Done"}, headers=headers)
        
        # Let's seed and detect 3 more times to hit the threshold > 3 past anomalies
        # We will directly add historical resolved anomalies to the DB to simulate recurrence over 7 days
        async with session_manager.session() as session:
            for i in range(3):
                past_viol = AnomalyDbModel(
                    id=str(uuid4()),
                    anomaly_type="WORKER_FAILURE_BURST",
                    severity="CRITICAL",
                    component="ar_event_processor",
                    detected_at=datetime.utcnow() - timedelta(hours=i+1),
                    source="RULE_ENGINE",
                    observed_value="3 failures",
                    expected_value="< 3",
                    evidence="{}",
                    organization_id=org_id,
                    status="RESOLVED"
                )
                session.add(past_viol)
            await session.commit()

        # Seed failures for the 5th anomaly
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Failed again",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # Trigger detection -> this 5th anomaly will check past 4 anomalies (1 resolved + 3 seeded = 4 resolved)
        # 4 resolved anomalies > 3 threshold -> triggers auto-escalation (escalated = True, priority = CRITICAL)
        await ac.post("/api/observability/anomalies/detect", headers=headers)
        
        anoms2 = (await ac.get("/api/observability/anomalies", headers=headers)).json()
        active_anom = [a for a in anoms2 if a["status"] == "DETECTED"][0]
        assert active_anom["recurrence_count"] == 4
        assert active_anom["escalated"] is True
        assert active_anom["priority"] == "CRITICAL"
        assert "auto_escalation_reason" in active_anom["evidence"]

        # 8. Test Recurring Patterns API
        res_patterns = await ac.get("/api/observability/anomalies/patterns/recurring", headers=headers)
        assert res_patterns.status_code == 200
        patterns = res_patterns.json()
        assert len(patterns) >= 1
        assert patterns[0]["component"] == "ar_event_processor"
        assert patterns[0]["anomaly_type"] == "WORKER_FAILURE_BURST"
        assert patterns[0]["count"] == 5
        assert patterns[0]["escalated_count"] >= 2 # the manual escalated first + the auto-escalated fifth
