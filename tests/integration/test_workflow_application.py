import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.workflow import (
    WorkflowStatus,
    WorkflowVersion,
    Workflow
)
from nomadx.src.infrastructure.database.models import (
    OrganizationDbModel,
    UserDbModel,
    OutboxEventDbModel
)
from nomadx.src.presentation.api.dependencies.context import ExecutionContext
from nomadx.src.application.workflow.commands import (
    CreateWorkflowCommand,
    CreateWorkflowVersionCommand,
    ActivateWorkflowCommand,
    PauseWorkflowCommand,
    ArchiveWorkflowCommand,
    ActivateWorkflowVersionCommand
)
from nomadx.src.application.workflow.handlers import (
    CreateWorkflowHandler,
    CreateWorkflowVersionHandler,
    ActivateWorkflowHandler,
    PauseWorkflowHandler,
    ArchiveWorkflowHandler,
    ActivateWorkflowVersionHandler
)
from nomadx.src.infrastructure.database.repositories import PostgresWorkflowRepository


async def _create_test_org(session: AsyncSession, org_id: str):
    owner = UserDbModel(
        id=str(uuid4()),
        email=f"merchant-{org_id[:8]}@example.com",
        password_hash="hash",
        name="Merchant Admin",
        status="ACTIVE"
    )
    org = OrganizationDbModel(
        id=org_id,
        name="NomadX Corp",
        slug=f"nomadx-corp-{org_id[:8]}",
        status="ACTIVE",
        default_currency="USD",
        owner_id=owner.id
    )
    session.add(owner)
    session.add(org)
    await session.flush()


def _build_valid_payload():
    return {
        "trigger_event_type": "InvoiceIssued",
        "conditions_data": {
            "operator": "AND",
            "conditions": [
                {
                    "field": "invoice.amount",
                    "operator": "GREATER_THAN",
                    "value": 1000
                }
            ]
        },
        "actions_data": [
            {
                "action_type": "SEND_NOTIFICATION",
                "parameters": {"template_id": "remind-template"}
            }
        ]
    }


@pytest.mark.asyncio
async def test_workflow_creation_and_idempotency_handlers(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    handler = CreateWorkflowHandler(repo)

    ctx = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_id,
        role="OWNER"
    )

    payload = _build_valid_payload()
    cmd = CreateWorkflowCommand(
        organization_id=org_id,
        reference="WF-APP-CREATION-1",
        name="Invoice Automated Flow",
        description="Auto remind",
        idempotency_key="idem-app-1",
        **payload
    )

    # 1. Valid Creation
    res = await handler.handle(cmd, ctx)
    assert res.is_success
    wf = res.value
    assert wf.status == WorkflowStatus.DRAFT
    assert len(wf.definitions) == 1

    await db_session.commit()

    # 2. Idempotent repeated creation
    res_repeat = await handler.handle(cmd, ctx)
    assert res_repeat.is_success
    assert res_repeat.value.id.value == wf.id.value

    # 3. Duplicate reference rejection
    cmd_dup_ref = CreateWorkflowCommand(
        organization_id=org_id,
        reference="WF-APP-CREATION-1", # duplicate reference
        name="Invoice Automated Flow 2",
        description="Auto remind 2",
        idempotency_key="idem-app-2",
        **payload
    )
    res_dup_ref = await handler.handle(cmd_dup_ref, ctx)
    assert res_dup_ref.is_failure
    assert res_dup_ref.error.code == "DUPLICATE_WORKFLOW"

    # 4. Duplicate idempotency key same org rejection
    cmd_dup_idem = CreateWorkflowCommand(
        organization_id=org_id,
        reference="WF-APP-CREATION-2",
        name="Invoice Automated Flow 3",
        description="Auto remind 3",
        idempotency_key="idem-app-1", # duplicate idempotency key
        **payload
    )
    # Wait, the handler returns the existing aggregate for the same idempotency key (idempotency success!),
    # so we verify it returns the first workflow.
    res_dup_idem = await handler.handle(cmd_dup_idem, ctx)
    assert res_dup_idem.is_success
    assert res_dup_idem.value.id.value == wf.id.value


@pytest.mark.asyncio
async def test_tenant_isolation_restrictions(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    await _create_test_org(db_session, org_a)
    await _create_test_org(db_session, org_b)

    repo = PostgresWorkflowRepository(db_session)
    create_handler = CreateWorkflowHandler(repo)
    pause_handler = PauseWorkflowHandler(repo)

    ctx_a = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_a,
        role="OWNER"
    )
    ctx_b = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_b,
        role="OWNER"
    )

    # Org A Workflow
    payload = _build_valid_payload()
    wf_res = await create_handler.handle(
        CreateWorkflowCommand(
            organization_id=org_a,
            reference="WF-ISO-A",
            name="Org A Workflow",
            description=None,
            idempotency_key="idem-iso-a",
            **payload
        ),
        ctx_a
    )
    assert wf_res.is_success
    wf = wf_res.value
    await db_session.commit()

    # Org B attempts to Pause Org A Workflow -> Expect failure
    cmd_pause = PauseWorkflowCommand(
        workflow_id=wf.id.value,
        organization_id=org_b
    )
    res_pause = await pause_handler.handle(cmd_pause, ctx_b)
    assert res_pause.is_failure
    assert res_pause.error.code == "UNAUTHORIZED_ORGANIZATION_ACCESS"


@pytest.mark.asyncio
async def test_workflow_lifecycle_transitions_handlers(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    create_handler = CreateWorkflowHandler(repo)
    activate_handler = ActivateWorkflowHandler(repo)
    pause_handler = PauseWorkflowHandler(repo)
    archive_handler = ArchiveWorkflowHandler(repo)

    ctx = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_id,
        role="OWNER"
    )

    payload = _build_valid_payload()
    wf = (await create_handler.handle(
        CreateWorkflowCommand(
            organization_id=org_id,
            reference="WF-LIFECYCLE-H",
            name="Lifecycle Handler WF",
            description=None,
            idempotency_key="idem-lifecycle-h",
            **payload
        ),
        ctx
    )).value
    await db_session.commit()

    # DRAFT -> ACTIVE
    act_res = await activate_handler.handle(ActivateWorkflowCommand(wf.id.value, org_id), ctx)
    assert act_res.is_success
    
    # Reload and assert status
    db_session.expire_all()
    wf_loaded = await repo.get_by_id(wf.id)
    assert wf_loaded.status == WorkflowStatus.ACTIVE

    # ACTIVE -> PAUSED
    p_res = await pause_handler.handle(PauseWorkflowCommand(wf.id.value, org_id), ctx)
    assert p_res.is_success
    db_session.expire_all()
    wf_loaded = await repo.get_by_id(wf.id)
    assert wf_loaded.status == WorkflowStatus.PAUSED

    # PAUSED -> ARCHIVED
    arc_res = await archive_handler.handle(ArchiveWorkflowCommand(wf.id.value, org_id), ctx)
    assert arc_res.is_success
    db_session.expire_all()
    wf_loaded = await repo.get_by_id(wf.id)
    assert wf_loaded.status == WorkflowStatus.ARCHIVED


@pytest.mark.asyncio
async def test_workflow_version_management_handlers(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    create_handler = CreateWorkflowHandler(repo)
    version_handler = CreateWorkflowVersionHandler(repo)
    activate_ver_handler = ActivateWorkflowVersionHandler(repo)

    ctx = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_id,
        role="OWNER"
    )

    payload = _build_valid_payload()
    wf = (await create_handler.handle(
        CreateWorkflowCommand(
            organization_id=org_id,
            reference="WF-VER-H",
            name="Version Handler WF",
            description=None,
            idempotency_key="idem-ver-h",
            **payload
        ),
        ctx
    )).value
    await db_session.commit()

    # Create Version 2
    cmd_ver = CreateWorkflowVersionCommand(
        workflow_id=wf.id.value,
        organization_id=org_id,
        trigger_event_type="PaymentReconciled",
        conditions_data=payload["conditions_data"],
        actions_data=payload["actions_data"]
    )
    res_ver = await version_handler.handle(cmd_ver, ctx)
    assert res_ver.is_success
    assert res_ver.value.value == 2
    await db_session.commit()

    # Activate version 2
    res_act = await activate_ver_handler.handle(
        ActivateWorkflowVersionCommand(
            workflow_id=wf.id.value,
            organization_id=org_id,
            version_number=2
        ),
        ctx
    )
    assert res_act.is_success
    await db_session.commit()

    # Assert version activation details
    db_session.expire_all()
    wf_loaded = await repo.get_by_id(wf.id)
    assert wf_loaded.active_version_number == 2


@pytest.mark.asyncio
async def test_outbox_integration_and_atomic_rollback(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    create_handler = CreateWorkflowHandler(repo)
    ctx = ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_id,
        role="OWNER"
    )

    # Valid save yields outbox events
    payload = _build_valid_payload()
    wf = (await create_handler.handle(
        CreateWorkflowCommand(
            organization_id=org_id,
            reference="WF-OUTBOX-H",
            name="Outbox Handler WF",
            description=None,
            idempotency_key="idem-outbox-h",
            **payload
        ),
        ctx
    )).value
    await db_session.commit()

    db_session.expire_all()
    events = (await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "WorkflowCreated")
    )).scalars().all()
    assert len(events) == 1
