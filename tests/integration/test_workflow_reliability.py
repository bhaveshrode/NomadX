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
from nomadx.src.infrastructure.database.repositories import PostgresWorkflowRepository, PostgresWorkflowExecutionRepository
from nomadx.src.infrastructure.workers.workflow_worker import WorkflowWorker


async def _create_test_org(db_session: AsyncSession, org_id: str) -> None:
    user_id = str(uuid4())
    user = UserDbModel(
        id=user_id,
        email=f"owner-{str(uuid4())[:8]}@example.com",
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


@pytest.mark.asyncio
async def test_workflow_automatic_retries_and_dead_lettering(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # 1. Create a workflow with an action that will fail (CREATE_SETTLEMENT without payment)
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-RELIABILITY-1"),
        name=WorkflowName("Settle Fail Workflow"),
        description="Fails and Retries",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value

    trigger = WorkflowTrigger("PaymentConfirmed")
    cond = Condition("payment_id", ConditionOperator.EXISTS, None)
    conditions = ConditionGroup(LogicalOperator.AND, [cond])
    action = WorkflowAction(ActionType.CREATE_SETTLEMENT, {"amount": "100.00", "asset_code": "USDC"})

    # Setup max attempts = 3
    version_res = wf.add_version(trigger=trigger, conditions=conditions, actions=[action])
    wf.activate_version(version_res.value.value)
    wf.activate()

    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # 2. Insert outbox event (PaymentConfirmed with non-existent payment UUID)
    evt_id = str(uuid4())
    event = OutboxEventDbModel(
        id=evt_id,
        event_type="PaymentConfirmed",
        payload=json.dumps({
            "organization_id": org_id,
            "payment_id": str(uuid4()),
            "observed_amount": "100.00"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )
    db_session.add(event)
    await db_session.commit()

    # 3. First execution run (Attempt 1 -> Should fail and transition to RETRYING)
    worker = WorkflowWorker()
    processed = await worker.process_pending_events()
    assert processed == 1

    db_session.expire_all()
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution.status == "RETRYING"
    assert execution.retry_count == 1
    assert execution.next_retry_at is not None
    assert "not found" in execution.failure_reason.lower() or "failed" in execution.failure_reason.lower()

    # 4. Advance execution state to retry count 2 to simulate previous failure cycles
    execution.retry_count = 2
    await db_session.commit()

    # Execute retry cycle (Claim due retries -> next attempt = 3 -> exhausts max_attempts -> DEAD_LETTERED)
    # Fast forward clock next_retry_at
    execution.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
    await db_session.commit()

    # Process due retries
    processed_retries = await worker.process_pending_events()
    assert processed_retries == 1

    db_session.expire_all()
    execution_refetched = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution_refetched.status == "DEAD_LETTERED"
    assert execution_refetched.dead_lettered_at is not None
    assert execution_refetched.failure_classification == "PERMANENT"


@pytest.mark.asyncio
async def test_workflow_delayed_scheduled_actions(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # 1. Create a workflow with a delayed SEND_NOTIFICATION (delay_seconds = 2)
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-DELAY-1"),
        name=WorkflowName("Delayed Notification"),
        description="Delays 2 seconds",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value

    trigger = WorkflowTrigger("InvoiceIssued")
    cond = Condition("invoice.amount", ConditionOperator.EXISTS, None)
    conditions = ConditionGroup(LogicalOperator.AND, [cond])
    action = WorkflowAction(ActionType.SEND_NOTIFICATION, {"template_id": "delay-remind", "delay_seconds": 2})

    version_res = wf.add_version(trigger=trigger, conditions=conditions, actions=[action])
    wf.activate_version(version_res.value.value)
    wf.activate()

    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # 2. Insert outbox event
    evt_id = str(uuid4())
    event = OutboxEventDbModel(
        id=evt_id,
        event_type="InvoiceIssued",
        payload=json.dumps({
            "organization_id": org_id,
            "invoice_id": str(uuid4()),
            "invoice": {"amount": 1200},
            "customer_email": "customer@delayed.com"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )
    db_session.add(event)
    await db_session.commit()

    # 3. Process event -> should result in action being SCHEDULED, not executed immediately
    worker = WorkflowWorker()
    processed = await worker.process_pending_events()
    assert processed == 1

    db_session.expire_all()
    # Verify execution is STARTED
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution.status == "STARTED"

    # Verify action result is logged as SCHEDULED
    action_results = json.loads(execution.action_results)
    assert len(action_results) == 1
    assert action_results[0]["status"] == "SCHEDULED"

    # Verify record in workflow_scheduled_actions
    stmt_sa = select(WorkflowScheduledActionDbModel).where(WorkflowScheduledActionDbModel.execution_id == execution.id)
    sa = (await db_session.execute(stmt_sa)).scalar_one()
    assert sa.status == "PENDING"
    assert sa.scheduled_at is not None

    # Verify no notification created in database yet
    stmt_notif = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_id)
    assert (await db_session.execute(stmt_notif)).scalar_one_or_none() is None

    # 4. Fast forward scheduled_at
    sa.scheduled_at = datetime.utcnow() - timedelta(seconds=1)
    await db_session.commit()

    # Process due scheduled actions
    processed_scheduled = await worker.process_pending_events()
    assert processed_scheduled == 1

    db_session.expire_all()
    # Verify execution is now COMPLETED
    execution_completed = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution_completed.status == "COMPLETED"

    # Verify notification has been created
    notification = (await db_session.execute(stmt_notif)).scalar_one()
    assert notification.recipient == "customer@delayed.com"
    assert notification.template_id == "delay-remind"


@pytest.mark.asyncio
async def test_workflow_api_retry_and_replay(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Onboard owner
        reg = await ac.post("/auth/register", json={
            "email": f"owner-{str(uuid4())[:8]}@nomadx.com",
            "password": "strongpassword123",
            "name": "Owner User",
            "organization_name": f"Org-{str(uuid4())[:8]}",
            "organization_slug": f"org-slug-{str(uuid4())[:8]}",
            "default_currency": "USD"
        })
        assert reg.status_code == 201

        login = await ac.post("/auth/login", json={
            "email": reg.json()["user"]["email"],
            "password": "strongpassword123"
        })
        token = login.json()["token"]

        ctx_owner = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token}"})
        org_id = ctx_owner.json()["organization_id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Create a workflow that executes immediately
        payload = {
            "reference": f"WF-API-RELIABILITY-{str(uuid4())[:8]}",
            "name": "API Automated Workflow",
            "description": "API Test",
            "trigger_event_type": "InvoiceIssued",
            "idempotency_key": f"api-idem-key-{str(uuid4())[:8]}",
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
        res_create = await ac.post("/api/workflows", json=payload, headers=headers)
        assert res_create.status_code == 201
        wf_id = res_create.json()["id"]

        # Activate workflow
        await ac.post(f"/api/workflows/{wf_id}/activate", headers=headers)

        # Trigger event Issuance
        evt_id = str(uuid4())
        event = OutboxEventDbModel(
            id=evt_id,
            event_type="InvoiceIssued",
            payload=json.dumps({
                "organization_id": org_id,
                "invoice_id": str(uuid4()),
                "invoice": {"amount": 1500},
                "customer_email": "api@reliability.com"
            }),
            processed=False,
            created_at=datetime.utcnow() - timedelta(minutes=5)
        )
        db_session.add(event)
        await db_session.commit()

        # Run process
        worker = WorkflowWorker()
        await worker.process_pending_events()

        # Fetch list of executions
        res_list = await ac.get("/api/workflow-executions", headers=headers)
        assert res_list.status_code == 200
        executions = res_list.json()
        assert len(executions) >= 1
        exec_id = executions[0]["id"]

        # Call get execution details
        res_details = await ac.get(f"/api/workflow-executions/{exec_id}", headers=headers)
        assert res_details.status_code == 200
        assert res_details.json()["status"] == "COMPLETED"

        # Force state to FAILED in the database to test manual retry redrive
        stmt = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.id == exec_id)
        exec_model = (await db_session.execute(stmt)).scalar_one()
        exec_model.status = "FAILED"
        await db_session.commit()

        # Call manual retry API
        res_retry = await ac.post(f"/api/workflow-executions/{exec_id}/retry", headers=headers)
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "COMPLETED"

        # Call manual replay API (creates a brand new execution attempt)
        res_replay = await ac.post(f"/api/workflow-executions/{exec_id}/replay", headers=headers)
        assert res_replay.status_code == 200
        assert res_replay.json()["id"] != exec_id
        assert res_replay.json()["status"] == "COMPLETED"
