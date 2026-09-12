import pytest
import json
from uuid import uuid4
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from nomadx.apps.api.main import app
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.workflow.value_objects import (
    WorkflowId, WorkflowReference, WorkflowName, WorkflowStatus,
    WorkflowVersion, WorkflowIdempotencyKey
)
from nomadx.src.domain.workflow.workflow import Workflow
from nomadx.src.domain.workflow.triggers import WorkflowTrigger
from nomadx.src.domain.workflow.conditions import Condition, ConditionGroup, ConditionOperator, LogicalOperator
from nomadx.src.domain.workflow.actions import WorkflowAction, ActionType
from nomadx.src.infrastructure.database.models import (
    OrganizationDbModel, UserDbModel, MembershipDbModel, OutboxEventDbModel,
    WorkflowDbModel, WorkflowVersionDbModel, WorkflowExecutionDbModel,
    WorkflowScheduledActionDbModel, NotificationDbModel, SettlementDbModel
)
from nomadx.src.infrastructure.database.repositories import PostgresWorkflowRepository
from nomadx.src.infrastructure.workers.workflow_worker import WorkflowWorker
from nomadx.src.application.workflow.variable_resolver import VariableResolver, ParameterTemplater


async def _create_test_org(db_session: AsyncSession, org_id: str) -> str:
    user_id = str(uuid4())
    email = f"owner-{str(uuid4())[:8]}@nomadx.com"
    user = UserDbModel(
        id=user_id,
        email=email,
        password_hash="fake-hash",
        name="Owner",
        status="ACTIVE",
        email_verified=True
    )
    org = OrganizationDbModel(
        id=org_id,
        name="Automation Corp",
        slug=f"auto-corp-{str(uuid4())[:8]}",
        default_currency="USD",
        status="ACTIVE",
        owner_id=user_id
    )
    membership = MembershipDbModel(
        organization_id=org_id,
        user_id=user_id,
        role="OWNER",
        status="ACTIVE"
    )
    db_session.add(user)
    db_session.add(org)
    db_session.add(membership)
    await db_session.commit()
    return email


def test_variable_resolver_and_templater():
    context = {
        "invoice": {
            "reference": "INV-100",
            "amount": 1500.50,
            "billing": {
                "email": "customer@test.com"
            }
        },
        "payment": {
            "id": "pay-uuid-abc"
        }
    }
    
    # 1. Nest resolution
    assert VariableResolver.resolve("event.invoice.reference", context) == "INV-100"
    assert VariableResolver.resolve("invoice.billing.email", context) == "customer@test.com"
    assert VariableResolver.resolve("payment.id", context) == "pay-uuid-abc"
    assert VariableResolver.resolve("non_existent.field", context) is None

    # 2. Parameter Templater type preservation
    resolved_amount = ParameterTemplater.resolve_template("{{event.invoice.amount}}", context)
    assert resolved_amount == 1500.50
    assert isinstance(resolved_amount, float)

    # 3. String interpolation
    resolved_str = ParameterTemplater.resolve_template("Subject: Invoice {{invoice.reference}} for {{invoice.billing.email}}", context)
    assert resolved_str == "Subject: Invoice INV-100 for customer@test.com"


@pytest.mark.asyncio
async def test_workflow_multiple_actions_and_failure_policies(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # 1. Create a workflow with multiple actions
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-ADVANCED-1"),
        name=WorkflowName("Multi Action Policies"),
        description="Tests advanced actions features",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value

    trigger = WorkflowTrigger("PaymentConfirmed")
    cond = Condition("payment_id", ConditionOperator.EXISTS, None)
    conditions = ConditionGroup(LogicalOperator.AND, [cond])
    
    # Action 1: Fails but specifies CONTINUE_WORKFLOW failure policy
    action_fail_continue = WorkflowAction(
        ActionType.CREATE_SETTLEMENT,
        {
            "amount": "100.00",
            "asset_code": "USDC",
            "failure_policy": "CONTINUE_WORKFLOW"
        }
    )
    # Action 2: SEND_NOTIFICATION which will succeed
    action_notif_success = WorkflowAction(
        ActionType.SEND_NOTIFICATION,
        {
            "recipient": "{{event.customer_email}}",
            "template_id": "success-notif",
            "subject": "Processed {{event.observed_amount}}",
            "failure_policy": "FAIL_WORKFLOW"
        }
    )

    version_res = wf.add_version(trigger=trigger, conditions=conditions, actions=[action_fail_continue, action_notif_success])
    wf.activate_version(version_res.value.value)
    wf.activate()

    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # 2. Insert outbox event (CREATE_SETTLEMENT action will fail because payment_id does not exist in DB)
    evt_id = str(uuid4())
    event = OutboxEventDbModel(
        id=evt_id,
        event_type="PaymentConfirmed",
        payload=json.dumps({
            "organization_id": org_id,
            "payment_id": str(uuid4()),
            "observed_amount": "200.00",
            "customer_email": "success-member@nomadx.com"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )
    db_session.add(event)
    await db_session.commit()

    # Run processor
    worker = WorkflowWorker()
    processed = await worker.process_pending_events()
    assert processed == 1

    db_session.expire_all()
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()

    # Assert execution completed successfully overall (because first failed action used CONTINUE_WORKFLOW)
    assert execution.status == "COMPLETED"
    
    # Assert action results detail success/failure mapping
    action_results = json.loads(execution.action_results)
    assert len(action_results) == 2
    assert action_results[0]["status"] == "FAILED"
    assert action_results[1]["status"] == "SUCCESS"
    assert "duration" in action_results[0]
    assert "duration" in action_results[1]

    # Assert notification was created
    stmt_notif = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_id)
    notif = (await db_session.execute(stmt_notif)).scalar_one()
    assert notif.recipient == "success-member@nomadx.com"
    assert notif.subject == "Processed 200.00"


@pytest.mark.asyncio
async def test_workflow_recursion_loop_protection_safeguard(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # Create simple workflow
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-RECURSION-LOOP-1"),
        name=WorkflowName("Recursion Loop Protec"),
        description="Abort loops",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value
    trigger = WorkflowTrigger("PaymentConfirmed")
    cond = Condition("payment_id", ConditionOperator.EXISTS, None)
    conditions = ConditionGroup(LogicalOperator.AND, [cond])
    action = WorkflowAction(ActionType.SEND_NOTIFICATION, {"template_id": "test"})
    version_res = wf.add_version(trigger=trigger, conditions=conditions, actions=[action])
    wf.activate_version(version_res.value.value)
    wf.activate()

    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # Insert outbox event payload with loop ancestry chain exceeding threshold (chain length = 5)
    evt_id = str(uuid4())
    event = OutboxEventDbModel(
        id=evt_id,
        event_type="PaymentConfirmed",
        payload=json.dumps({
            "organization_id": org_id,
            "payment_id": str(uuid4()),
            "_metadata": {
                "correlation_id": "root-correlation-uuid",
                "ancestry": ["exec-1", "exec-2", "exec-3", "exec-4", "exec-5"]
            }
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )
    db_session.add(event)
    await db_session.commit()

    # Run processor
    worker = WorkflowWorker()
    processed = await worker.process_pending_events()
    assert processed == 1

    # Verify execution was immediately aborted and created in DEAD_LETTERED status
    db_session.expire_all()
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution.status == "DEAD_LETTERED"
    assert "recursion" in execution.failure_reason.lower()


@pytest.mark.asyncio
async def test_workflow_api_validation_and_metrics(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner-{str(uuid4())[:8]}@nomadx.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner User",
            "organization_name": f"Org-{str(uuid4())[:8]}",
            "organization_slug": f"org-slug-{str(uuid4())[:8]}",
            "default_currency": "USD"
        })
        assert reg.status_code == 201
        org_id = reg.json()["organization"]["id"]

        # Onboard login
        login = await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Attempt to create and activate a workflow with unbalanced templating syntax
        payload_invalid_template = {
            "reference": f"WF-VAL-1-{str(uuid4())[:8]}",
            "name": "Invalid Template Workflow",
            "idempotency_key": str(uuid4()),
            "trigger_event_type": "PaymentConfirmed",
            "conditions_data": {"operator": "AND", "conditions": [{"field": "payment_id", "operator": "EXISTS"}]},
            "actions_data": [
                {
                    "action_type": "SEND_NOTIFICATION",
                    "parameters": {"template_id": "test", "recipient": "recipient-{{event.email"} # unbalanced bracket
                }
            ]
        }
        res_create = await ac.post("/api/workflows", json=payload_invalid_template, headers=headers)
        assert res_create.status_code == 201
        wf_id = res_create.json()["id"]

        # Call activate -> should validate and return 400 Bad Request due to unbalanced curly braces
        res_activate = await ac.post(f"/api/workflows/{wf_id}/activate", headers=headers)
        assert res_activate.status_code == 400
        assert "unbalanced curly braces" in res_activate.json()["detail"].lower()

        # 2. Verify Metrics query aggregation
        res_metrics = await ac.get(f"/api/workflows/{wf_id}/metrics", headers=headers)
        assert res_metrics.status_code == 200
        metrics = res_metrics.json()
        assert metrics["total_executions"] == 0
        assert metrics["successful_executions"] == 0
