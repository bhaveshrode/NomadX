import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from datetime import datetime, timezone, timedelta
import json

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    FinancialConsistencyViolationDbModel,
    AnomalyDbModel
)


async def _seed_consistency_violation(org_id: str, ent_id: str) -> None:
    async with session_manager.session() as session:
        viol = FinancialConsistencyViolationDbModel(
            id=str(uuid4()),
            organization_id=org_id,
            violation_type="Amount-Mismatch",
            context="Receivable",
            entity_id=ent_id,
            severity="CRITICAL",
            classification="AMOUNT_MISMATCH",
            expected_value="1000.00",
            actual_value="900.00",
            description="Mismatch detected",
            resolved=False,
            detected_at=datetime.utcnow()
        )
        session.add(viol)
        await session.commit()


@pytest.mark.asyncio
async def test_anomaly_detection_and_lifecycle_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant A
        email_a = f"owner_anom_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A Anomalies",
            "organization_slug": f"org-anom-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        org_a = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # 2. Register Tenant B
        email_b = f"owner_anom_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B Anomalies",
            "organization_slug": f"org-anom-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # --- SEED SCENARIOS ---
        # Seed 3 failed worker telemetry events (triggers WORKER_FAILURE_BURST)
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck processing execution failed",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_a
                )

        # Seed 2 financial consistency violations (triggers REPEATED_VIOLATIONS)
        await _seed_consistency_violation(org_a, "inv-obs-1")
        await _seed_consistency_violation(org_a, "inv-obs-2")

        # --- TEST 1: RUN DETECTOR SCAN ---
        res_detect = await ac.post("/api/observability/anomalies/detect", headers=headers_a)
        assert res_detect.status_code == 200
        det_data = res_detect.json()
        assert det_data["detected_anomalies_count"] == 2

        # --- TEST 2: GET ANOMALIES LIST ---
        res_list = await ac.get("/api/observability/anomalies", headers=headers_a)
        assert res_list.status_code == 200
        anoms = res_list.json()
        assert len(anoms) == 2
        
        types = [a["anomaly_type"] for a in anoms]
        assert "WORKER_FAILURE_BURST" in types
        assert "REPEATED_VIOLATIONS" in types

        # Find worker burst anomaly ID
        burst_anom = [a for a in anoms if a["anomaly_type"] == "WORKER_FAILURE_BURST"][0]
        burst_id = burst_anom["id"]

        # --- TEST 3: CORRELATION / DEDUPLICATION ---
        # Log another failed worker event
        async with container.telemetry_service() as telemetry_service:
            await telemetry_service.record_event(
                component="WORKER",
                operation="process_payment_events",
                message="Another failed process payment events",
                severity="ERROR",
                status="FAILED",
                organization_id=org_a
            )
        
        # Trigger scan again -> anomalies count remains 2 (updates existing one)
        res_detect_2 = await ac.post("/api/observability/anomalies/detect", headers=headers_a)
        assert res_detect_2.json()["detected_anomalies_count"] == 2
        assert res_detect_2.json()["anomalies"][0]["action"] == "CORRELATED"

        # Check detail contains correlated update log
        res_detail = await ac.get(f"/api/observability/anomalies/{burst_id}", headers=headers_a)
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert "updates" in detail["evidence"]
        assert len(detail["evidence"]["updates"]) == 1

        # --- TEST 4: LIFECYCLE TRANSITIONS ---
        # 1. Transition to ACKNOWLEDGED
        res_ack = await ac.post(
            f"/api/observability/anomalies/{burst_id}/status",
            json={"status": "ACKNOWLEDGED", "notes": "Acknowledged by operator"},
            headers=headers_a
        )
        assert res_ack.status_code == 200
        assert res_ack.json()["current_status"] == "ACKNOWLEDGED"

        # 2. Transition to RESOLVED
        res_resolve = await ac.post(
            f"/api/observability/anomalies/{burst_id}/status",
            json={"status": "RESOLVED", "notes": "Reset config successfully"},
            headers=headers_a
        )
        assert res_resolve.status_code == 200
        
        # Confirm in DB
        res_detail2 = await ac.get(f"/api/observability/anomalies/{burst_id}", headers=headers_a)
        assert res_detail2.json()["status"] == "RESOLVED"
        assert res_detail2.json()["resolution_notes"] == "Reset config successfully"

        # 3. Transition from RESOLVED back to DETECTED -> should fail
        res_fail_transition = await ac.post(
            f"/api/observability/anomalies/{burst_id}/status",
            json={"status": "DETECTED", "notes": "Try rollback status"},
            headers=headers_a
        )
        assert res_fail_transition.status_code == 400
        assert "invalid lifecycle transition" in res_fail_transition.json()["detail"].lower()

        # --- TEST 5: TENANT ISOLATION BOUNDARY ---
        # Query Tenant A's anomalies using Tenant B's credentials -> should be empty
        res_list_b = await ac.get("/api/observability/anomalies", headers=headers_b)
        assert len(res_list_b.json()) == 0

        # Query Tenant A's specific anomaly using Tenant B's credentials -> should fail with 404
        res_detail_b = await ac.get(f"/api/observability/anomalies/{burst_id}", headers=headers_b)
        assert res_detail_b.status_code == 404
