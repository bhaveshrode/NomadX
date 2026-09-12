import pytest
import uuid
from sqlalchemy import select

from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import UserDbModel, OrganizationDbModel, MembershipDbModel, OutboxEventDbModel
from nomadx.src.infrastructure.database.repositories import PostgresUserRepository, PostgresOrganizationRepository
from nomadx.src.application.identity.commands import RegisterMerchantCommand, RegisterMerchantHandler

@pytest.mark.asyncio
async def test_registration_flow_success():
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    command = RegisterMerchantCommand(
        user_id=user_id,
        email="registration_test@example.com",
        password_plain="secure-password-123",
        name="Bob Merchant",
        organization_id=org_id,
        organization_name="Bob Business",
        organization_slug="bob-business-slug",
        default_currency="USD"
    )

    async with session_manager.session() as session:
        user_repo = PostgresUserRepository(session)
        org_repo = PostgresOrganizationRepository(session)
        handler = RegisterMerchantHandler(user_repo, org_repo)

        result = await handler.handle(command)
        assert result.is_success
        
        dto = result.value
        assert dto["user"]["id"] == user_id
        assert dto["organization"]["id"] == org_id
        assert dto["user"]["status"] == "PENDING"
        assert dto["organization"]["status"] == "ACTIVE"

    # Verify database session persisted UserDbModel in a separate session context
    async with session_manager.session() as check_session:
        user_stmt = select(UserDbModel).where(UserDbModel.id == user_id)
        db_user = (await check_session.execute(user_stmt)).scalar_one_or_none()
        assert db_user is not None
        assert db_user.email == "registration_test@example.com"
        assert db_user.name == "Bob Merchant"

        # Verify database session persisted OrganizationDbModel
        org_stmt = select(OrganizationDbModel).where(OrganizationDbModel.id == org_id)
        db_org = (await check_session.execute(org_stmt)).scalar_one_or_none()
        assert db_org is not None
        assert db_org.slug == "bob-business-slug"

        # Verify database session persisted MembershipDbModel
        mem_stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == org_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        db_mem = (await check_session.execute(mem_stmt)).scalar_one_or_none()
        assert db_mem is not None
        assert db_mem.role == "OWNER"

        # Verify database session persisted OutboxEventDbModel for both events
        outbox_stmt = select(OutboxEventDbModel)
        outbox_records = (await check_session.execute(outbox_stmt)).scalars().all()
        
        event_types = [r.event_type for r in outbox_records]
        assert "UserRegistered" in event_types
        assert "OrganizationCreated" in event_types


@pytest.mark.asyncio
async def test_registration_flow_rollback_on_conflict():
    # Setup: Create a pre-existing user email
    user_id_existing = str(uuid.uuid4())
    org_id_existing = str(uuid.uuid4())
    
    async with session_manager.session() as setup_session:
        user_repo = PostgresUserRepository(setup_session)
        org_repo = PostgresOrganizationRepository(setup_session)
        handler = RegisterMerchantHandler(user_repo, org_repo)
        
        command_setup = RegisterMerchantCommand(
            user_id=user_id_existing,
            email="conflict_test@example.com",
            password_plain="secure-password-123",
            name="Alice Merchant",
            organization_id=org_id_existing,
            organization_name="Alice Business",
            organization_slug="alice-business-slug",
            default_currency="USD"
        )
        await handler.handle(command_setup)

    user_id_new = str(uuid.uuid4())
    org_id_new = str(uuid.uuid4())
    command_conflict = RegisterMerchantCommand(
        user_id=user_id_new,
        email="conflict_test@example.com",  # Duplicate email
        password_plain="secure-password-abc",
        name="Bob Merchant",
        organization_id=org_id_new,
        organization_name="Bob Business",
        organization_slug="bob-business-new-slug",
        default_currency="USD"
    )

    # Attempt to register with the same email address
    async with session_manager.session() as session:
        user_repo = PostgresUserRepository(session)
        org_repo = PostgresOrganizationRepository(session)
        handler = RegisterMerchantHandler(user_repo, org_repo)

        result = await handler.handle(command_conflict)
        assert result.is_failure
        assert "already registered" in result.error.message

    # Verify in a new session context that organization_id_new was NOT persisted (rolled back)
    async with session_manager.session() as check_session:
        org_stmt = select(OrganizationDbModel).where(OrganizationDbModel.id == org_id_new)
        db_org = (await check_session.execute(org_stmt)).scalar_one_or_none()
        assert db_org is None
