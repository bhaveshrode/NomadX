import pytest
import json
from uuid import uuid4
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    NotificationDbModel, SettlementDbModel, PaymentDbModel
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
async def test_workflow_execution_engine_e2e_scenarios(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # 1. Create and Save an ACTIVE Workflow in Database
    # We will build a workflow definition:
    # Trigger: InvoiceIssued
    # Conditions: (invoice.amount > 1000) AND (invoice.currency == "USD")
    # Actions: SEND_NOTIFICATION
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-EXEC-E2E-1"),
        name=WorkflowName("E2E Auto Remind"),
        description="E2E Execution Test Workflow",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value

    # Add version definition
    trigger = WorkflowTrigger("InvoiceIssued")
    cond1 = Condition("invoice.amount", ConditionOperator.GREATER_THAN, "1000")
    cond2 = Condition("invoice.currency", ConditionOperator.EQUALS, "USD")
    conditions = ConditionGroup(LogicalOperator.AND, [cond1, cond2])
    action = WorkflowAction(ActionType.SEND_NOTIFICATION, {"template_id": "remind-template", "subject": "High Value Invoice Issued"})
    
    version_res = wf.add_version(
        trigger=trigger,
        conditions=conditions,
        actions=[action]
    )
    version = version_res.value
    
    # Activate version and workflow
    wf.activate_version(version.value)
    wf.activate()

    # Save using repository
    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # 2. Insert outbox events
    # Event A: InvoiceIssued with amount = 1500 (Matches!)
    evt_a_id = str(uuid4())
    event_a = OutboxEventDbModel(
        id=evt_a_id,
        event_type="InvoiceIssued",
        payload=json.dumps({
            "organization_id": org_id,
            "invoice_id": str(uuid4()),
            "invoice": {"amount": 1500, "currency": "USD"},
            "customer_email": "customer@buyer.com"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )

    # Event B: InvoiceIssued with amount = 500 (Conditions mismatch!)
    evt_b_id = str(uuid4())
    event_b = OutboxEventDbModel(
        id=evt_b_id,
        event_type="InvoiceIssued",
        payload=json.dumps({
            "organization_id": org_id,
            "invoice_id": str(uuid4()),
            "invoice": {"amount": 500, "currency": "USD"},
            "customer_email": "customer@buyer.com"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=4)
    )

    # Event C: PaymentReconciled (Trigger mismatch!)
    evt_c_id = str(uuid4())
    event_c = OutboxEventDbModel(
        id=evt_c_id,
        event_type="PaymentReconciled",
        payload=json.dumps({
            "organization_id": org_id,
            "payment_id": str(uuid4())
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=3)
    )

    db_session.add(event_a)
    db_session.add(event_b)
    db_session.add(event_c)
    await db_session.commit()

    # Initialize worker and run execution engine
    worker = WorkflowWorker()
    processed_count = await worker.process_pending_events()
    assert processed_count == 1 # Only Event A should trigger execution

    # Verify execution record is persisted in COMPLETED status
    db_session.expire_all()
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_a_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution.status == "COMPLETED", f"Failure reason: {execution.failure_reason}"
    assert execution.version == 1
    assert execution.workflow_id == wf_id.value
    
    # Verify notification aggregate is created as action outcome
    stmt_notif = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_a_id)
    notification = (await db_session.execute(stmt_notif)).scalar_one()
    assert notification.recipient == "customer@buyer.com"
    assert notification.subject == "High Value Invoice Issued"
    assert notification.template_id == "remind-template"

    # Verify other events did not create execution logs
    stmt_exec_b = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_b_id)
    assert (await db_session.execute(stmt_exec_b)).scalar_one_or_none() is None

    stmt_exec_c = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_c_id)
    assert (await db_session.execute(stmt_exec_c)).scalar_one_or_none() is None

    # 3. Test Idempotency: Re-run events processing (should process 0 events)
    reprocess_count = await worker.process_pending_events()
    assert reprocess_count == 0


@pytest.mark.asyncio
async def test_workflow_execution_failed_action_savepoint_rollback(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # Create active workflow with action CREATE_SETTLEMENT
    # But we will not provide a Payment in the database, causing the action handler to fail (availability check fails).
    wf_id = WorkflowId(str(uuid4()))
    wf_res = Workflow.create(
        id=wf_id,
        organization_id=OrganizationId(org_id),
        reference=WorkflowReference("WF-EXEC-E2E-2"),
        name=WorkflowName("E2E Auto Settle"),
        description="E2E Failed Action Test Workflow",
        idempotency_key=WorkflowIdempotencyKey(str(uuid4()))
    )
    wf = wf_res.value

    trigger = WorkflowTrigger("PaymentConfirmed")
    # Conditions: ALWAYS TRUE (using EXISTS on payment_id)
    cond = Condition("payment_id", ConditionOperator.EXISTS, None)
    conditions = ConditionGroup(LogicalOperator.AND, [cond])
    # Settlement action
    action = WorkflowAction(ActionType.CREATE_SETTLEMENT, {"amount": "200.00", "asset_code": "USDC"})

    version_res = wf.add_version(trigger=trigger, conditions=conditions, actions=[action])
    wf.activate_version(version_res.value.value)
    wf.activate()

    wf_repo = PostgresWorkflowRepository(db_session)
    await wf_repo.save(wf)
    await db_session.commit()

    # Insert outbox event for PaymentConfirmed but refer to a non-existent payment ID
    evt_id = str(uuid4())
    fake_payment_id = str(uuid4())
    event = OutboxEventDbModel(
        id=evt_id,
        event_type="PaymentConfirmed",
        payload=json.dumps({
            "organization_id": org_id,
            "payment_id": fake_payment_id,
            "observed_amount": "200.00"
        }),
        processed=False,
        created_at=datetime.utcnow() - timedelta(minutes=5)
    )
    db_session.add(event)
    await db_session.commit()

    # Process events: should run and fail the settlement creation due to missing payment,
    # but the execution record must still be saved in FAILED status!
    worker = WorkflowWorker()
    processed = await worker.process_pending_events()
    assert processed == 1

    db_session.expire_all()
    stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.source_event_id == evt_id)
    execution = (await db_session.execute(stmt_exec)).scalar_one()
    assert execution.status == "RETRYING"
    assert "not found" in execution.failure_reason.lower() or "failed" in execution.failure_reason.lower()

    # Verify no settlement was persisted (transaction rolled back the settlement write)
    stmt_settl = select(SettlementDbModel).where(SettlementDbModel.source_payment_id == fake_payment_id)
    assert (await db_session.execute(stmt_settl)).scalar_one_or_none() is None
