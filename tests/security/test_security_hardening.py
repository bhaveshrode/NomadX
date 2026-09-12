import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
import json
from datetime import datetime
import jwt
import time

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import MembershipDbModel, AnomalyDbModel, RemediationActionDbModel, TelemetryEventDbModel, OutboxEventDbModel

@pytest.mark.asyncio
async def test_security_hardening_suite():
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        
        # =====================================================================
        # 1. SETUP TENANTS A, B, AND MEMBER C
        # =====================================================================
        
        # Tenant A (Owner A, Admin Role)
        email_a = f"owner_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A",
            "organization_slug": f"org-a-{uuid4().hex[:6]}",
            "default_currency": "USD"
        })
        org_a = reg_a.json()["organization"]["id"]
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # Tenant B (Owner B)
        email_b = f"owner_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B",
            "organization_slug": f"org-b-{uuid4().hex[:6]}",
            "default_currency": "USD"
        })
        org_b = reg_b.json()["organization"]["id"]
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # Member C (Normal User inside Tenant A's Organization)
        email_c = f"member_c_{uuid4().hex[:6]}@example.com"
        reg_c = await ac.post("/auth/register", json={
            "email": email_c,
            "password": "strongpassword123",
            "name": "Member C",
            "organization_name": "Temp Org C",
            "organization_slug": f"org-c-{uuid4().hex[:6]}",
            "default_currency": "USD"
        })
        user_c_id = reg_c.json()["user"]["id"]
        token_c = (await ac.post("/auth/login", json={
            "email": email_c,
            "password": "strongpassword123"
        })).json()["token"]

        # Move Member C into Tenant A's organization as a MEMBER
        async with session_manager.session() as db_session:
            # Delete their own organization membership first
            from sqlalchemy import delete
            await db_session.execute(delete(MembershipDbModel).where(MembershipDbModel.user_id == user_c_id))
            
            # Add them to Org A as MEMBER
            new_memb = MembershipDbModel(
                organization_id=org_a,
                user_id=user_c_id,
                role="MEMBER",
                status="ACTIVE"
            )
            db_session.add(new_memb)
            await db_session.commit()

        headers_c = {"Authorization": f"Bearer {token_c}", "X-Organization-ID": org_a}

        # =====================================================================
        # 2. VALIDATE AUTHENTICATION & INVALID/EXPIRED TOKENS
        # =====================================================================
        
        # Unauthenticated request
        res = await ac.get("/api/observability/anomalies")
        assert res.status_code == 401

        # Invalid Credentials
        res = await ac.post("/auth/login", json={"email": email_a, "password": "wrongpassword"})
        assert res.status_code == 401

        # Invalid token signature/format
        res = await ac.get("/api/observability/anomalies", headers={"Authorization": "Bearer invalid_token_format"})
        assert res.status_code == 401

        # Expired credentials
        expired_payload = {
            "sub": user_c_id,
            "email": email_c,
            "name": "Member C",
            "exp": int(time.time()) - 3600  # 1 hour ago
        }
        expired_token = jwt.encode(expired_payload, container.settings.jwt_secret, algorithm="HS256")
        res = await ac.get("/api/observability/anomalies", headers={"Authorization": f"Bearer {expired_token}"})
        assert res.status_code == 401

        # =====================================================================
        # 3. PRIVILEGE ESCALATION — NORMAL USER VS ADMINISTRATIVE ENDPOINTS
        # =====================================================================
        
        # Seed an anomaly for Tenant A to act upon
        anomaly_id = str(uuid4())
        corr_id = f"corr-sec-{uuid4().hex[:6]}"
        async with session_manager.session() as db_session:
            anom = AnomalyDbModel(
                id=anomaly_id,
                anomaly_type="WORKER_FAILURE_BURST",
                severity="WARNING",
                component="ar_event_processor",
                organization_id=org_a,
                correlation_id=corr_id,
                status="DETECTED",
                evidence="{}"
            )
            db_session.add(anom)
            await db_session.commit()

        # Seed a failed remediation action for Tenant A
        remediation_id = str(uuid4())
        async with session_manager.session() as db_session:
            rem = RemediationActionDbModel(
                id=remediation_id,
                action_type="RECOVER_WORKER",
                target_component="ar_event_processor",
                exception_id=anomaly_id,
                trigger_condition="BURST",
                status="FAILED",
                attempt_count=1,
                initiating_rule="RULE_1",
                correlation_id=corr_id,
                organization_id=org_a
            )
            db_session.add(rem)
            await db_session.commit()

        # Normal User C (MEMBER) attempts to run anomaly detection -> 403 Forbidden
        res = await ac.post("/api/observability/anomalies/detect", headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts exception assignment -> 403 Forbidden
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/assign", json={"owner_id": "operator-1"}, headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts mitigation -> 403 Forbidden
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/mitigate", json={"notes": "mitigated by member"}, headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts escalation -> 403 Forbidden
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/escalate", json={"reason": "member escalate"}, headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts remediation retry -> 403 Forbidden
        res = await ac.post(f"/api/observability/remediations/{remediation_id}/retry", headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts autonomous policy update -> 403 Forbidden
        res = await ac.post("/api/observability/intelligence/policy", json={"readiness_level": "LEVEL_1_ASSISTED", "emergency_disable": True}, headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts telemetry cleanup -> 403 Forbidden
        res = await ac.post("/api/observability/telemetry/cleanup", json={"older_than_days": 10}, headers=headers_c)
        assert res.status_code == 403

        # Normal User C attempts recovery actions -> 403 Forbidden
        res = await ac.post("/api/financial-recovery/recheck", headers=headers_c)
        assert res.status_code == 403

        res = await ac.post(f"/api/financial-recovery/retry-event/{str(uuid4())}", headers=headers_c)
        assert res.status_code == 403

        res = await ac.post(f"/api/financial-recovery/replay-event/{str(uuid4())}", headers=headers_c)
        assert res.status_code == 403

        res = await ac.post("/api/financial-recovery/recover-stuck", headers=headers_c)
        assert res.status_code == 403

        res = await ac.get("/api/financial-recovery/remediations", headers=headers_c)
        assert res.status_code == 403

        # =====================================================================
        # 4. PRIVILEGE ESCALATION — TENANT ADMIN VS PLATFORM-LEVEL OPERATIONS
        # =====================================================================
        
        # Tenant A Admin attempts direct organization creation -> 403 Forbidden
        res = await ac.post("/api/organizations", json={
            "name": "Malicious Startup",
            "slug": "malicious-startup",
            "owner_id": "some-id",
            "default_currency": "USD"
        }, headers=headers_a)
        assert res.status_code == 403

        # Tenant A Admin attempts system-wide consistency sweep -> 403 Forbidden
        res = await ac.post("/api/financial-consistency/automation/run", headers=headers_a)
        assert res.status_code == 403

        # =====================================================================
        # 5. TENANT BOUNDARY ENFORCEMENT — CROSS-TENANT ATTEMPTS
        # =====================================================================
        
        # Tenant B attempts to read Tenant A's anomaly -> 404 Not Found
        res = await ac.get(f"/api/observability/anomalies/{anomaly_id}", headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to update Tenant A's anomaly status -> 404 Not Found
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/status", json={"status": "ACKNOWLEDGED"}, headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to assign Tenant A's anomaly -> 404 Not Found
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/assign", json={"owner_id": "operator-b"}, headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to mitigate Tenant A's anomaly -> 404 Not Found
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/mitigate", json={"notes": "mitigated by b"}, headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to escalate Tenant A's anomaly -> 404 Not Found
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/escalate", json={"reason": "escalate by b"}, headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to retry Tenant A's remediation -> 404 Not Found
        res = await ac.post(f"/api/observability/remediations/{remediation_id}/retry", headers=headers_b)
        assert res.status_code == 404

        # Seed an outbox event for Tenant A to verify recovery boundaries
        outbox_event_id = str(uuid4())
        async with session_manager.session() as db_session:
            ev = OutboxEventDbModel(
                id=outbox_event_id,
                event_type="ReceivableCreated",
                payload=json.dumps({
                    "organization_id": org_a,
                    "receivable_id": str(uuid4())
                }),
                processed=False,
                status="FAILED",
                attempts=1
            )
            db_session.add(ev)
            await db_session.commit()

        # Tenant B attempts to retry Tenant A's failed outbox event -> 404 Not Found
        res = await ac.post(f"/api/financial-recovery/retry-event/{outbox_event_id}", headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to replay Tenant A's outbox event -> 404 Not Found
        res = await ac.post(f"/api/financial-recovery/replay-event/{outbox_event_id}", headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to read Tenant A's recovery remediations audit trail -> 403 Forbidden
        headers_b_attack = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_a}
        res = await ac.get("/api/financial-recovery/remediations", headers=headers_b_attack)
        assert res.status_code == 403

        # Tenant A Admin successfully retries Tenant A's failed outbox event -> 200 OK
        res = await ac.post(f"/api/financial-recovery/retry-event/{outbox_event_id}", headers=headers_a)
        assert res.status_code == 200

        # =====================================================================
        # 6. OPERATIONAL CONTROL & AUDIT LOG INTEGRITY
        # =====================================================================
        
        # Tenant A Admin performs exception assignment
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/assign", json={"owner_id": "operator-a"}, headers=headers_a)
        assert res.status_code == 200

        # Tenant A Admin performs mitigation
        res = await ac.post(f"/api/observability/anomalies/{anomaly_id}/mitigate", json={"notes": "mitigated by owner a"}, headers=headers_a)
        assert res.status_code == 200

        # Tenant A Admin performs policy update
        res = await ac.post("/api/observability/intelligence/policy", json={"readiness_level": "LEVEL_1_ASSISTED", "emergency_disable": True}, headers=headers_a)
        assert res.status_code == 200

        # Verify that these administrative actions produced audit records with exact Actor, Org, Action, Target, and Correlation ID
        res_audit = await ac.get("/api/observability/control-plane/audit-trail", headers=headers_a)
        assert res_audit.status_code == 200
        audits = res_audit.json()
        assert len(audits) >= 3

        # Confirm details of the latest audit record (corresponds to UPDATE_READINESS_POLICY)
        policy_audit = audits[0]
        assert policy_audit["actor_id"] == reg_a.json()["user"]["id"] # Real user ID, not hardcoded "operator"
        assert policy_audit["action"] == "UPDATE_READINESS_POLICY"
        assert policy_audit["target_type"] == "policy"
        assert "emergency_disable" in policy_audit["new_state"]

        # Confirm details of the MITIGATE_EXCEPTION audit record
        mitigate_audit = audits[1]
        assert mitigate_audit["actor_id"] == reg_a.json()["user"]["id"]
        assert mitigate_audit["action"] == "MITIGATE_EXCEPTION"
        assert mitigate_audit["target_id"] == anomaly_id
        assert mitigate_audit["correlation_id"] == corr_id # Extracted from anomaly record!

        # Verify that Tenant B's audit history remains completely separate and empty
        res_audit_b = await ac.get("/api/observability/control-plane/audit-trail", headers=headers_b)
        assert len(res_audit_b.json()) == 0

        # =====================================================================
        # 7. API SECURITY (INPUT LIMITS & EXCEPTION MASKING)
        # =====================================================================
        
        # Malformed Input -> 422 Unprocessable Entity
        res = await ac.post("/api/observability/intelligence/policy", content="invalid_json_format", headers=headers_a)
        assert res.status_code == 422

        # Oversized Input (> 1MB) -> 413 Request Entity Too Large
        large_payload = "a" * (1024 * 1024 + 100) # Slightly larger than 1MB
        res = await ac.post("/api/observability/intelligence/policy", content=large_payload, headers=headers_a)
        assert res.status_code == 413
        assert "payload too large" in res.json()["detail"].lower()

        # Exception Masking: Cause an uncaught database/internal exception
        # We can trigger this by calling evaluate situation but making the database layer fail or passing an invalid ID
        # Wait, if we pass an invalid UUID structure to an endpoint, does it raise an unexpected exception?
        # Let's mock a method to raise an unexpected ValueError or Database exception to verify 500 translation
        # Let's call GET anomalies with a limit that fails Pydantic validation (which raises RequestValidationError -> 422)
        # To test unexpected exception mapping, we can patch/mock or call a route that raises a non-HTTPException.
        # Let's trigger a ValueError in situation timeline by passing a situation ID that is not a valid situation
        # Actually, let's call situation timeline with a non-existent situation ID -> raises ValueError -> translated to 404.
        # What if we pass an completely invalid ID or cause a real unhandled exception?
        # Let's test a mock unhandled exception route or let's trust our Exception handler works.
        # We can make a request to a non-existent route -> 404 (handled by FastAPI).
        # We can test exception mapping using a mocked query service call. Let's patch get_recurring_patterns to raise a RuntimeError
        # and verify it returns a clean 500.
        from unittest.mock import patch
        with patch("nomadx.src.infrastructure.database.observability_query_service.PostgresObservabilityQueryService.get_recurring_patterns") as mock_patterns:
            mock_patterns.side_effect = RuntimeError("Raw database connection lost. SQL query failed.")
            res = await ac.get("/api/observability/anomalies/patterns/recurring", headers=headers_a)
            assert res.status_code == 500
            assert res.json() == {"detail": "An unexpected error occurred. Please contact support."} # Masked! Stack trace not leaked.
