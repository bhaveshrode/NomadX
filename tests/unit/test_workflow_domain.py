import pytest
from uuid import uuid4
from datetime import datetime

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
    WorkflowAction,
    WorkflowCreated,
    WorkflowActivated,
    WorkflowPaused,
    WorkflowArchived,
    WorkflowVersionCreated,
    WorkflowVersionActivated
)


def _build_valid_definition_params():
    trigger = WorkflowTrigger("InvoiceIssued")
    cond1 = Condition("invoice.amount", ConditionOperator.GREATER_THAN, 1000)
    conditions = ConditionGroup(LogicalOperator.AND, [cond1])
    action = WorkflowAction(ActionType.SEND_NOTIFICATION, {"template_id": "remind-template"})
    return trigger, conditions, [action]


def test_workflow_creation_success():
    org_id = OrganizationId(str(uuid4()))
    ref = WorkflowReference("WF-2026-000001")
    name = WorkflowName("Invoice Reminder Workflow")
    idem = WorkflowIdempotencyKey("idem-wf-1")

    res = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=org_id,
        reference=ref,
        name=name,
        description="Remind customers",
        idempotency_key=idem
    )
    assert res.is_success
    wf = res.value
    assert wf.status == WorkflowStatus.DRAFT
    assert len(wf.domain_events) == 1
    assert isinstance(wf.domain_events[0], WorkflowCreated)


def test_workflow_creation_with_version_one():
    org_id = OrganizationId(str(uuid4()))
    ref = WorkflowReference("WF-2026-000002")
    name = WorkflowName("Invoice Reminder Workflow")
    idem = WorkflowIdempotencyKey("idem-wf-2")
    trigger, conditions, actions = _build_valid_definition_params()

    res = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=org_id,
        reference=ref,
        name=name,
        description="Remind customers",
        idempotency_key=idem,
        trigger=trigger,
        conditions=conditions,
        actions=actions
    )
    assert res.is_success
    wf = res.value
    assert len(wf.definitions) == 1
    assert 1 in wf.definitions
    assert wf.definitions[1].trigger.event_type == "InvoiceIssued"
    assert len(wf.domain_events) == 2  # Created + VersionCreated
    assert isinstance(wf.domain_events[0], WorkflowCreated)
    assert isinstance(wf.domain_events[1], WorkflowVersionCreated)


def test_workflow_creation_invalid_reference():
    with pytest.raises(ValueError, match="must start with 'WF-'"):
        WorkflowReference("INVALID-REF-123")


def test_workflow_creation_empty_name():
    with pytest.raises(ValueError, match="cannot be empty"):
        WorkflowName("")


def test_workflow_creation_too_long_name():
    with pytest.raises(ValueError, match="cannot exceed 100 characters"):
        WorkflowName("a" * 101)


def test_workflow_lifecycle_transitions():
    org_id = OrganizationId(str(uuid4()))
    trigger, conditions, actions = _build_valid_definition_params()
    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=org_id,
        reference=WorkflowReference("WF-2026-100"),
        name=WorkflowName("Lifecyle WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-lifecycle"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # 1. DRAFT -> ACTIVE (Success)
    assert wf.status == WorkflowStatus.DRAFT
    res_act = wf.activate()
    assert res_act.is_success
    assert wf.status == WorkflowStatus.ACTIVE
    assert wf.active_version_number == 1
    assert isinstance(wf.domain_events[-2], WorkflowVersionActivated)
    assert isinstance(wf.domain_events[-1], WorkflowActivated)

    # 2. ACTIVE -> PAUSED (Success)
    res_pause = wf.pause()
    assert res_pause.is_success
    assert wf.status == WorkflowStatus.PAUSED
    assert isinstance(wf.domain_events[-1], WorkflowPaused)

    # 3. PAUSED -> ACTIVE (Success)
    res_act2 = wf.activate()
    assert res_act2.is_success
    assert wf.status == WorkflowStatus.ACTIVE

    # 4. ACTIVE -> ARCHIVED (Success)
    res_arc = wf.archive()
    assert res_arc.is_success
    assert wf.status == WorkflowStatus.ARCHIVED
    assert isinstance(wf.domain_events[-1], WorkflowArchived)


def test_workflow_invalid_lifecycle_transitions():
    org_id = OrganizationId(str(uuid4()))
    trigger, conditions, actions = _build_valid_definition_params()
    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=org_id,
        reference=WorkflowReference("WF-2026-101"),
        name=WorkflowName("Lifecyle WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-lifecycle-invalid"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # DRAFT -> PAUSED (Invalid)
    res_p = wf.pause()
    assert res_p.is_failure
    assert "Cannot pause" in res_p.error.message

    # DRAFT -> ARCHIVED (Invalid)
    res_arc = wf.archive()
    assert res_arc.is_failure
    assert "Cannot archive" in res_arc.error.message

    # DRAFT -> ACTIVE
    wf.activate()

    # ACTIVE -> ARCHIVED
    wf.archive()

    # ARCHIVED -> ACTIVE (Invalid)
    res_act = wf.activate()
    assert res_act.is_failure
    assert "Cannot activate" in res_act.error.message

    # ARCHIVED -> PAUSED (Invalid)
    res_p2 = wf.pause()
    assert res_p2.is_failure
    assert "Cannot pause" in res_p2.error.message


def test_conditions_validation():
    # Invalid field name
    with pytest.raises(ValueError, match="cannot be empty"):
        Condition("", ConditionOperator.EQUALS, "value")

    # Missing value for operator requiring value
    with pytest.raises(ValueError, match="Comparison value is required"):
        Condition("invoice.amount", ConditionOperator.GREATER_THAN, None)

    # Valid EXISTS without value
    cond = Condition("invoice.custom_field", ConditionOperator.EXISTS)
    assert cond.operator == ConditionOperator.EXISTS
    assert cond.value is None


def test_actions_validation():
    # Unsupported parameter type
    with pytest.raises(ValueError, match="parameters must be a dictionary"):
        WorkflowAction(ActionType.SEND_NOTIFICATION, "invalid-params")

    # Missing template_id parameter for SEND_NOTIFICATION
    with pytest.raises(ValueError, match="template_id parameter is required"):
        WorkflowAction(ActionType.SEND_NOTIFICATION, {"priority": "HIGH"})


def test_version_progression_and_immutability():
    org_id = OrganizationId(str(uuid4()))
    trigger, conditions, actions = _build_valid_definition_params()
    wf = Workflow.create(
        id=WorkflowId(str(uuid4())),
        organization_id=org_id,
        reference=WorkflowReference("WF-2026-300"),
        name=WorkflowName("Version WF"),
        description=None,
        idempotency_key=WorkflowIdempotencyKey("idem-version"),
        trigger=trigger,
        conditions=conditions,
        actions=actions
    ).value

    # Check version 1
    assert len(wf.definitions) == 1
    assert 1 in wf.definitions

    # Add version 2
    trigger2 = WorkflowTrigger("PaymentReconciled")
    res_ver = wf.add_version(trigger2, conditions, actions)
    assert res_ver.is_success
    assert res_ver.value.value == 2
    assert len(wf.definitions) == 2
    assert wf.definitions[2].trigger.event_type == "PaymentReconciled"

    # Activate version 2
    res_act_ver = wf.activate_version(2)
    assert res_act_ver.is_success
    assert wf.active_version_number == 2

    # Verify that trying to activate non-existent version fails
    res_act_bad = wf.activate_version(99)
    assert res_act_bad.is_failure
    assert "not found" in res_act_bad.error.message

    # Activate and then archive workflow
    wf.activate()
    wf.archive()
    # Adding a version to an archived workflow must fail
    res_add_arch = wf.add_version(trigger, conditions, actions)
    assert res_add_arch.is_failure
    assert "archived workflow" in res_add_arch.error.message
