import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OrganizationDbModel, OutboxEventDbModel

@pytest.mark.asyncio
async def test_health_check_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "nomadx-api"
        assert "timestamp" in data
        assert data["dependencies"]["database"] == "healthy"

@pytest.mark.asyncio
async def test_create_organization_api_flow():
    # Make sure container DB settings are initialized to our test DB (handled by conftest setup_test_db fixture)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register a platform admin
        reg_payload = {
            "email": "platform_admin@example.com",
            "password": "strongpassword123",
            "name": "Platform Admin",
            "organization_name": "Platform Org",
            "organization_slug": "platform-org",
            "default_currency": "USD"
        }
        await ac.post("/auth/register", json=reg_payload)
        login_res = await ac.post("/auth/login", json={
            "email": "platform_admin@example.com",
            "password": "strongpassword123"
        })
        token = login_res.json()["token"]
        user_id = login_res.json()["user"]["id"]

        # Promote user to PLATFORM_ADMIN in DB
        async with session_manager.session() as db_session:
            from nomadx.src.infrastructure.database.models import MembershipDbModel
            from sqlalchemy import update
            stmt = (
                update(MembershipDbModel)
                .where(MembershipDbModel.user_id == user_id)
                .values(role="PLATFORM_ADMIN")
            )
            await db_session.execute(stmt)
            await db_session.commit()

        headers = {"Authorization": f"Bearer {token}"}
        owner_id = str(uuid.uuid4())
        payload = {
            "name": "My New Startup",
            "slug": "my-new-startup",
            "owner_id": owner_id,
            "default_currency": "USD"
        }

        # Create Organization
        response = await ac.post("/api/organizations", json=payload, headers=headers)
        assert response.status_code == 201
        
        dto = response.json()
        assert dto["name"] == "My New Startup"
        assert dto["slug"] == "my-new-startup"
        assert dto["owner_id"] == owner_id
        assert dto["default_currency"] == "USD"
        assert dto["status"] == "ACTIVE"
        assert "id" in dto

        # Verify saved in DB
        async with session_manager.session() as session:
            result = await session.execute(
                select(OrganizationDbModel).where(OrganizationDbModel.id == dto["id"])
            )
            db_org = result.scalar_one_or_none()
            assert db_org is not None
            assert db_org.name == "My New Startup"
            assert db_org.slug == "my-new-startup"

            # Verify Outbox captured the event (Note: we registered 1 org in register call, 1 org in direct create)
            outbox_result = await session.execute(select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "OrganizationCreated"))
            outbox_records = outbox_result.scalars().all()
            assert len(outbox_records) == 2

        # Try to register duplicate slug -> Should yield 409 Conflict
        response_dup = await ac.post("/api/organizations", json=payload, headers=headers)
        assert response_dup.status_code == 409
        assert "is already taken" in response_dup.json()["detail"]

        # Try with invalid slug format (domain validation) -> Should yield 400 Bad Request
        payload_bad = payload.copy()
        payload_bad["slug"] = "INVALID SLUG"  # Fails domain regex but passes Pydantic length
        
        response_bad = await ac.post("/api/organizations", json=payload_bad, headers=headers)
        assert response_bad.status_code == 400
        assert "slug can only contain lowercase" in response_bad.json()["detail"].lower()
