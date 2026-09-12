from datetime import datetime
from typing import Optional
from ..shared.events.domain_event import DomainEvent
from .value_objects import WorkflowId, WorkflowVersion
from ..organization.value_objects import OrganizationId


class WorkflowCreated(DomainEvent):
    """Event emitted when a workflow is first created in DRAFT status."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)


class WorkflowActivated(DomainEvent):
    """Event emitted when a workflow is activated."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)


class WorkflowPaused(DomainEvent):
    """Event emitted when a workflow is paused."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)


class WorkflowArchived(DomainEvent):
    """Event emitted when a workflow is permanently archived."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)


class WorkflowVersionCreated(DomainEvent):
    """Event emitted when a new version definition is added to a workflow."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        version: WorkflowVersion,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id
        self.version = version

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)


class WorkflowVersionActivated(DomainEvent):
    """Event emitted when a specific version definition is set as current/active."""
    def __init__(
        self,
        workflow_id: WorkflowId,
        version: WorkflowVersion,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.workflow_id = workflow_id
        self.version = version

    def get_aggregate_id(self) -> str:
        return str(self.workflow_id.value)
