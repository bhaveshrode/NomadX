from uuid import uuid4
from typing import List, Optional

from ...domain.shared.result.result import Result, ResultError
from ...domain.organization.value_objects import OrganizationId
from ...domain.workflow import (
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
    IWorkflowRepository
)
from ...presentation.api.dependencies.context import ExecutionContext
from .commands import (
    CreateWorkflowCommand,
    CreateWorkflowVersionCommand,
    ActivateWorkflowCommand,
    PauseWorkflowCommand,
    ArchiveWorkflowCommand,
    ActivateWorkflowVersionCommand
)
from .errors import WorkflowErrors


def parse_trigger(event_type: str) -> WorkflowTrigger:
    if not event_type:
        raise ValueError("Trigger event_type cannot be empty.")
    return WorkflowTrigger(event_type)


def parse_conditions(data: dict) -> ConditionGroup:
    if not data or "operator" not in data:
        raise ValueError("ConditionGroup operator is required.")
    
    operator = LogicalOperator(data["operator"])
    conditions = []
    
    for item in data.get("conditions", []):
        # Determine if it is a single condition or a nested group
        if "field" in item:
            conditions.append(Condition(
                field=item["field"],
                operator=ConditionOperator(item["operator"]),
                value=item.get("value")
            ))
        elif "operator" in item:
            conditions.append(parse_conditions(item))
            
    return ConditionGroup(operator=operator, conditions=conditions)


def parse_actions(data_list: List[dict]) -> List[WorkflowAction]:
    if not data_list:
        raise ValueError("Workflow must contain at least one action.")
    
    actions = []
    for act in data_list:
        actions.append(WorkflowAction(
            action_type=ActionType(act["action_type"]),
            parameters=act.get("parameters", {})
        ))
    return actions


class CreateWorkflowHandler:
    """Handler to create a new Workflow in DRAFT status."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: CreateWorkflowCommand, ctx: ExecutionContext) -> Result[Workflow]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        # Idempotency lookup
        existing = await self._workflow_repo.find_by_idempotency_key(
            OrganizationId(command.organization_id),
            WorkflowIdempotencyKey(command.idempotency_key)
        )
        if existing:
            return Result.ok(existing)

        # Reference uniqueness check
        ref_existing = await self._workflow_repo.find_by_reference(WorkflowReference(command.reference))
        if ref_existing:
            return Result.fail(WorkflowErrors.duplicate_workflow(f"Workflow reference {command.reference} already exists."))

        # Parse triggers, conditions, actions if provided
        trigger = None
        conditions = None
        actions = None
        try:
            if command.trigger_event_type:
                trigger = parse_trigger(command.trigger_event_type)
            if command.conditions_data:
                conditions = parse_conditions(command.conditions_data)
            if command.actions_data:
                actions = parse_actions(command.actions_data)
        except (ValueError, KeyError) as ex:
            return Result.fail(WorkflowErrors.invalid_workflow_data(str(ex)))

        # Create Workflow
        wf_id = WorkflowId(str(uuid4()))
        wf_res = Workflow.create(
            id=wf_id,
            organization_id=OrganizationId(command.organization_id),
            reference=WorkflowReference(command.reference),
            name=WorkflowName(command.name),
            description=command.description,
            idempotency_key=WorkflowIdempotencyKey(command.idempotency_key),
            trigger=trigger,
            conditions=conditions,
            actions=actions
        )

        if wf_res.is_failure:
            return Result.fail(wf_res.error)

        workflow = wf_res.value
        await self._workflow_repo.save(workflow)
        return Result.ok(workflow)


class CreateWorkflowVersionHandler:
    """Handler to add a new immutable definition version to a workflow."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: CreateWorkflowVersionCommand, ctx: ExecutionContext) -> Result[WorkflowVersion]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        # Load under pessimistic locking
        workflow = await self._workflow_repo.get_by_id(WorkflowId(command.workflow_id), lock=True)
        if not workflow:
            return Result.fail(WorkflowErrors.workflow_not_found())

        # Tenant ownership validation
        if workflow.organization_id.value != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access())

        # Immutability validation: cannot add version to archived
        if workflow.status == WorkflowStatus.ARCHIVED:
            return Result.fail(WorkflowErrors.workflow_already_archived())

        # Parse definition
        try:
            trigger = parse_trigger(command.trigger_event_type)
            conditions = parse_conditions(command.conditions_data)
            actions = parse_actions(command.actions_data)
        except (ValueError, KeyError) as ex:
            return Result.fail(WorkflowErrors.invalid_workflow_data(str(ex)))

        # Add version definition
        ver_res = workflow.add_version(trigger, conditions, actions)
        if ver_res.is_failure:
            return Result.fail(ver_res.error)

        await self._workflow_repo.save(workflow)
        return Result.ok(ver_res.value)


class ActivateWorkflowHandler:
    """Handler to transition draft/paused workflow status to ACTIVE."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: ActivateWorkflowCommand, ctx: ExecutionContext) -> Result[None]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        workflow = await self._workflow_repo.get_by_id(WorkflowId(command.workflow_id), lock=True)
        if not workflow:
            return Result.fail(WorkflowErrors.workflow_not_found())

        if workflow.organization_id.value != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access())

        if workflow.status == WorkflowStatus.ACTIVE:
            return Result.ok(None)

        act_res = workflow.activate()
        if act_res.is_failure:
            return Result.fail(act_res.error)

        await self._workflow_repo.save(workflow)
        return Result.ok(None)


class PauseWorkflowHandler:
    """Handler to transition active workflow status to PAUSED."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: PauseWorkflowCommand, ctx: ExecutionContext) -> Result[None]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        workflow = await self._workflow_repo.get_by_id(WorkflowId(command.workflow_id), lock=True)
        if not workflow:
            return Result.fail(WorkflowErrors.workflow_not_found())

        if workflow.organization_id.value != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access())

        if workflow.status == WorkflowStatus.PAUSED:
            return Result.ok(None)

        pause_res = workflow.pause()
        if pause_res.is_failure:
            return Result.fail(pause_res.error)

        await self._workflow_repo.save(workflow)
        return Result.ok(None)


class ArchiveWorkflowHandler:
    """Handler to permanently transition active/paused workflow status to ARCHIVED."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: ArchiveWorkflowCommand, ctx: ExecutionContext) -> Result[None]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        workflow = await self._workflow_repo.get_by_id(WorkflowId(command.workflow_id), lock=True)
        if not workflow:
            return Result.fail(WorkflowErrors.workflow_not_found())

        if workflow.organization_id.value != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access())

        if workflow.status == WorkflowStatus.ARCHIVED:
            return Result.ok(None)

        arc_res = workflow.archive()
        if arc_res.is_failure:
            return Result.fail(arc_res.error)

        await self._workflow_repo.save(workflow)
        return Result.ok(None)


class ActivateWorkflowVersionHandler:
    """Handler to select a specific definition version as active."""
    def __init__(self, workflow_repo: IWorkflowRepository):
        self._workflow_repo = workflow_repo

    async def handle(self, command: ActivateWorkflowVersionCommand, ctx: ExecutionContext) -> Result[None]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access("Context tenant mismatch."))

        workflow = await self._workflow_repo.get_by_id(WorkflowId(command.workflow_id), lock=True)
        if not workflow:
            return Result.fail(WorkflowErrors.workflow_not_found())

        if workflow.organization_id.value != ctx.organization_id:
            return Result.fail(WorkflowErrors.unauthorized_access())

        if workflow.status == WorkflowStatus.ARCHIVED:
            return Result.fail(WorkflowErrors.workflow_already_archived())

        # If version requested is already active, return success
        if workflow.active_version_number == command.version_number:
            return Result.ok(None)

        ver_res = workflow.activate_version(command.version_number)
        if ver_res.is_failure:
            return Result.fail(ver_res.error)

        await self._workflow_repo.save(workflow)
        return Result.ok(None)
