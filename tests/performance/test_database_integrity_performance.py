import pytest
import time
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import TelemetryEventDbModel

@pytest.mark.asyncio
async def test_pagination_and_limit_bounds():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_dbperf_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner DbPerf",
            "organization_name": "Org DbPerf Test",
            "organization_slug": f"org-dbperf-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed 120 telemetry events
        async with session_manager.session() as session:
            for i in range(120):
                ev = TelemetryEventDbModel(
                    id=str(uuid4()),
                    timestamp=datetime.utcnow() - timedelta(seconds=i),
                    component="API",
                    operation="test_perf",
                    message=f"Event {i}",
                    severity="INFO",
                    status="SUCCESS",
                    organization_id=org_id
                )
                session.add(ev)
            await session.commit()

        # Query with limit=150 -> should return 422 Unprocessable Entity due to router limits
        res = await ac.get("/api/observability/traces?limit=150", headers=headers)
        assert res.status_code == 422

        # Query with limit=100 -> should return exactly 100 elements
        res2 = await ac.get("/api/observability/traces?limit=100", headers=headers)
        assert res2.status_code == 200
        assert len(res2.json()) == 100

        # Query service method directly with limit=150 -> should be capped at 100 internally
        from nomadx.src.domain.organization.value_objects import OrganizationId
        async with container.observability_query_service() as query_service:
            direct_results = await query_service.query_traces(
                organization_id=OrganizationId(org_id),
                limit=150
            )
            assert len(direct_results) == 100

@pytest.mark.asyncio
async def test_retention_telemetry_cleanup():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_dbclean_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner DbClean",
            "organization_name": "Org DbClean Test",
            "organization_slug": f"org-dbclean-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed 1 old event (40 days ago) and 1 recent event (10 days ago)
        async with session_manager.session() as session:
            ev_old = TelemetryEventDbModel(
                id=str(uuid4()),
                timestamp=datetime.utcnow() - timedelta(days=40),
                component="API",
                operation="old_event",
                message="Old event",
                severity="INFO",
                status="SUCCESS",
                organization_id=org_id
            )
            ev_new = TelemetryEventDbModel(
                id=str(uuid4()),
                timestamp=datetime.utcnow() - timedelta(days=10),
                component="API",
                operation="new_event",
                message="New event",
                severity="INFO",
                status="SUCCESS",
                organization_id=org_id
            )
            session.add(ev_old)
            session.add(ev_new)
            await session.commit()

        # Call cleanup endpoint with retention = 30 days
        res_cleanup = await ac.post("/api/observability/telemetry/cleanup", json={"older_than_days": 30}, headers=headers)
        assert res_cleanup.status_code == 200
        res_data = res_cleanup.json()
        assert res_data["deleted_telemetry_events"] == 1

        # Assert only the recent event remains in the DB
        async with session_manager.session() as session:
            stmt = select(TelemetryEventDbModel).where(TelemetryEventDbModel.organization_id == org_id)
            db_events = (await session.execute(stmt)).scalars().all()
            assert len(db_events) == 1
            assert db_events[0].operation == "new_event"

@pytest.mark.asyncio
async def test_large_scale_query_performance():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_dbscale_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner DbScale",
            "organization_name": "Org DbScale Test",
            "organization_slug": f"org-dbscale-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed 1000 telemetry events
        async with session_manager.session() as session:
            for i in range(1000):
                ev = TelemetryEventDbModel(
                    id=str(uuid4()),
                    timestamp=datetime.utcnow() - timedelta(seconds=i),
                    component="WORKER",
                    operation="large_perf",
                    message=f"Event {i}",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )
                session.add(ev)
            await session.commit()

        # Query traces multiple times and assert response latency
        latencies = []
        for _ in range(5):
            start = time.perf_counter()
            res = await ac.get("/api/observability/traces?component=WORKER&limit=50", headers=headers)
            end = time.perf_counter()
            assert res.status_code == 200
            latencies.append(end - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000.0
        # Assert average latency is < 150ms (proving indexed lookup holds well)
        assert avg_latency_ms < 150.0
