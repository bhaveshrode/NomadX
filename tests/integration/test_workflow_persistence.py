import pytest
from uuid import uuid4
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.workflow import (
    Workflow,
    WorkflowId,
    WorkflowReference,
    WorkflowName,
    WorkflowStatus,
    WorkflowVersion,
    WorkflowIdempotencyKey,
    WorkflowTrigger,
    ConditionOperator,
    Condition,
    LogicalOperator,
    ConditionGroup,
    ActionType,
    WorkflowAction
)
from nomadx.src.infrastructure.database.models import (
    WorkflowDbModel,
    WorkflowVersionDbModel,
    OutboxEventDbModel,
    OrganizationDbModel,
    UserDbModel
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


def _build_valid_definition_params():
    trigger = WorkflowTrigger("InvoiceIssued")
    cond1 = Condition("invoice.amount", ConditionOperator.GREATER_THAN, 1000)
    conditions = ConditionGroup(LogicalOperator.AND, [cond1])
    action = WorkflowAction(ActionType.SEND_NOTIFICATION, {"template_id": "remind-template"})
    return trigger, conditions, [action]


@pytest.mark.asyncio
async def test_workflow_persistence_and_reconstruction(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    trigger, conditions, actions = _build_valid_definition_params()

    # Create Workflow with version 1
    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-2026-PERSIST-1"),
        name=WorkflowName("Payment Followup"),
        description="Follow up on unpaid large invoices",
        idempotency_key=WorkflowIdempotencyKey("idem-persist-1"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # Save to PostgreSQL
    await repo.save(wf)
    await db_session.commit()

    # Retrieve and verify aggregate reconstruction
    db_session.expire_all()
    retrieved = await repo.get_by_id(wf.id)
    assert retrieved is not None
    assert retrieved.id.value == wf.id.value
    assert retrieved.organization_id.value == org_id
    assert retrieved.reference.value == "WF-2026-PERSIST-1"
    assert retrieved.name.value == "Payment Followup"
    assert retrieved.description == "Follow up on unpaid large invoices"
    assert retrieved.status == WorkflowStatus.DRAFT
    assert retrieved.active_version_number is None

    # Verify version 1
    assert len(retrieved.definitions) == 1
    v1 = retrieved.definitions[1]
    assert v1.version.value == 1
    assert v1.trigger.event_type == "InvoiceIssued"
    assert v1.conditions.operator == LogicalOperator.AND
    assert len(v1.conditions.conditions) == 1
    assert v1.conditions.conditions[0].field == "invoice.amount"
    assert v1.conditions.conditions[0].operator == ConditionOperator.GREATER_THAN
    assert v1.conditions.conditions[0].value == 1000
    assert len(v1.actions) == 1
    assert v1.actions[0].action_type == ActionType.SEND_NOTIFICATION
    assert v1.actions[0].parameters == {"template_id": "remind-template"}


@pytest.mark.asyncio
async def test_workflow_multiple_versions_and_uniqueness(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    trigger, conditions, actions = _build_valid_definition_params()

    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-2026-VERSION-1"),
        name=WorkflowName("Versioning Test"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-version-1"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # Save Version 1
    await repo.save(wf)
    await db_session.commit()

    # Add Version 2
    trigger2 = WorkflowTrigger("PaymentReconciled")
    res_ver = wf.add_version(trigger2, conditions, actions)
    assert res_ver.is_success
    wf.activate_version(2)

    await repo.save(wf)
    await db_session.commit()

    # Reload and assert multiple versions
    db_session.expire_all()
    retrieved = await repo.get_by_id(wf.id)
    assert len(retrieved.definitions) == 2
    assert retrieved.active_version_number == 2
    assert retrieved.definitions[1].trigger.event_type == "InvoiceIssued"
    assert retrieved.definitions[2].trigger.event_type == "PaymentReconciled"


@pytest.mark.asyncio
async def test_tenant_isolation_rules(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    await _create_test_org(db_session, org_a)
    await _create_test_org(db_session, org_b)

    repo = PostgresWorkflowRepository(db_session)
    trigger, conditions, actions = _build_valid_definition_params()

    # Org A Workflow
    wf_a = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_a),
        reference=WorkflowReference("WF-2026-TENANT-A"),
        name=WorkflowName("Tenant A WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-tenant-same"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value
    await repo.save(wf_a)

    # Same Idempotency Key across different Org is allowed
    wf_b = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_b),
        reference=WorkflowReference("WF-2026-TENANT-B"),
        name=WorkflowName("Tenant B WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-tenant-same"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value
    await repo.save(wf_b)
    await db_session.commit()

    # Verify query scopes correctly
    db_session.expire_all()
    
    # Retrieve Org A by Idempotency Key
    res_a = await repo.find_by_idempotency_key(OrganizationId(org_a), WorkflowIdempotencyKey("idem-tenant-same"))
    assert res_a is not None
    assert res_a.organization_id.value == org_a

    # Retrieve Org B by Idempotency Key
    res_b = await repo.find_by_idempotency_key(OrganizationId(org_b), WorkflowIdempotencyKey("idem-tenant-same"))
    assert res_b is not None
    assert res_b.organization_id.value == org_b


@pytest.mark.asyncio
async def test_uniqueness_constraints_and_idempotency_keys(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    trigger, conditions, actions = _build_valid_definition_params()

    wf1 = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-DUPLICATE-REF"),
        name=WorkflowName("WF 1"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-uniq-1"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value
    await repo.save(wf1)
    await db_session.commit()

    # 1. Duplicate reference is rejected
    wf2 = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-DUPLICATE-REF"), # duplicate
        name=WorkflowName("WF 2"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-uniq-2"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value
    with pytest.raises(ValueError, match="DUPLICATE_WORKFLOW_REFERENCE"):
        await repo.save(wf2)
        await db_session.commit()
    await db_session.rollback()

    # 2. Duplicate tenant-scoped idempotency key is rejected
    wf3 = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-DIFFERENT-REF"),
        name=WorkflowName("WF 3"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-uniq-1"), # duplicate key for same org
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value
    with pytest.raises(ValueError, match="DUPLICATE_WORKFLOW_IDEMPOTENCY"):
        await repo.save(wf3)
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_locking_and_outbox_integration(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    repo = PostgresWorkflowRepository(db_session)
    trigger, conditions, actions = _build_valid_definition_params()

    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-LOCK-1"),
        name=WorkflowName("Locking WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-lock-1"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # Save (Creates WorkflowCreated and WorkflowVersionCreated events)
    await repo.save(wf)
    await db_session.commit()

    # 1. Verify Outbox events exist
    db_session.expire_all()
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type.in_(["WorkflowCreated", "WorkflowVersionCreated"]))
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 2

    # 2. Verify pessimistic locking get_by_id(lock=True) works without errors
    async with db_session.begin_nested():
        locked_wf = await repo.get_by_id(wf.id, lock=True)
        assert locked_wf is not None
        assert locked_wf.id.value == wf.id.value


@pytest.mark.asyncio
async def test_physical_deletion_rejection(db_session: AsyncSession):
    repo = PostgresWorkflowRepository(db_session)
    # Deleting should raise ValueError
    with pytest.raises(ValueError, match="Deleting a Workflow record is rejected"):
        await repo.delete(WorkflowId(str(uuid4())))
