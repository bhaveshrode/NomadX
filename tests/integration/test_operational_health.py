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
    ComponentHealthTransitionDbModel
)


@pytest.mark.asyncio
async def test_operational_health_and_readiness_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Tenant
        email = f"owner_health_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Health",
            "organization_name": "Org Health",
            "organization_slug": f"org-health-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # --- TEST 1: INITIAL CLEAN HEALTH CHECK ---
        res_health = await ac.get("/api/observability/health", headers=headers)
        assert res_health.status_code == 200
        health = res_health.json()
        assert health["status"] == "HEALTHY"
        assert health["readiness"]["status"] == "READY"
        assert len(health["readiness"]["barriers"]) == 0
        assert health["components"]["database"]["status"] == "HEALTHY"
        assert health["components"]["api"]["status"] == "HEALTHY"
        # Workers are INACTIVE since no telemetry event exists yet
        assert health["components"]["ar_event_processor"]["status"] == "INACTIVE"

        # --- TEST 2: TRANSITION LOGS GENERATED ---
        res_history = await ac.get("/api/observability/health/history", headers=headers)
        assert res_history.status_code == 200
        history = res_history.json()
        assert len(history) >= 2 # database and platform transitions logged (from UNKNOWN to HEALTHY)
        
        db_transitions = [t for t in history if t["component"] == "database"]
        assert len(db_transitions) == 1
        assert db_transitions[0]["previous_state"] == "UNKNOWN"
        assert db_transitions[0]["resulting_state"] == "HEALTHY"

        # --- TEST 3: SEED CRITICAL FINANCIAL VIOLATION -> BLOCKS READINESS ---
        async with session_manager.session() as session:
            viol = FinancialConsistencyViolationDbModel(
                id=str(uuid4()),
                organization_id=org_id,
                violation_type="Invoice-Receivable",
                context="Invoice",
                entity_id="inv-obs-1",
                severity="CRITICAL",
                classification="AMOUNT_MISMATCH",
                expected_value="1000.00",
                actual_value="900.00",
                description="Amount mismatch detected",
                resolved=False,
                detected_at=datetime.utcnow()
            )
            session.add(viol)
            await session.commit()

        # Check health again -> Platform remains HEALTHY/DEGRADED but readiness is NOT_READY
        res_health2 = await ac.get("/api/observability/health", headers=headers)
        health2 = res_health2.json()
        assert health2["readiness"]["status"] == "NOT_READY"
        assert any("Active financial consistency violations present" in b for b in health2["readiness"]["barriers"])

        # --- TEST 4: SEED WORKER FAILURE -> DEGRADES COMPONENT & PLATFORM HEALTH ---
        async with container.telemetry_service() as telemetry_service:
            await telemetry_service.record_event(
                component="WORKER",
                operation="process_payment_events",
                message="Critical processing failure",
                severity="ERROR",
                status="FAILED",
                organization_id=org_id
            )

        res_health3 = await ac.get("/api/observability/health", headers=headers)
        health3 = res_health3.json()
        assert health3["components"]["ar_event_processor"]["status"] == "DEGRADED"
        assert health3["status"] == "DEGRADED"
        assert "ar_event_processor" in health3["evidence"]

        # Check transition history contains ar_event_processor transition
        res_history2 = await ac.get("/api/observability/health/history?component=ar_event_processor", headers=headers)
        history2 = res_history2.json()
        assert len(history2) >= 1
        assert history2[0]["previous_state"] == "INACTIVE"
        assert history2[0]["resulting_state"] == "DEGRADED"
