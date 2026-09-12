from abc import ABC, abstractmethod
from typing import List, Optional
from .workflow import Workflow
from .value_objects import WorkflowId, WorkflowReference, WorkflowStatus, WorkflowIdempotencyKey
from ..organization.value_objects import OrganizationId


class IWorkflowRepository(ABC):
    """
    Interface/Port for Workflow repository.
    Enforces bounded-context integrity for Workflow persistence.
    """

    @abstractmethod
    async def get_by_id(self, id: WorkflowId) -> Optional[Workflow]:
        """Retrieves a Workflow by its unique ID."""
        pass

    @abstractmethod
    async def find_by_reference(self, reference: WorkflowReference) -> Optional[Workflow]:
        """Retrieves a Workflow by its merchant-facing reference."""
        pass

    @abstractmethod
    async def find_by_organization(self, organization_id: OrganizationId) -> List[Workflow]:
        """Retrieves all Workflows belonging to a specific organization."""
        pass

    @abstractmethod
    async def find_by_status(
        self,
        organization_id: OrganizationId,
        status: WorkflowStatus
    ) -> List[Workflow]:
        """Retrieves all Workflows belonging to an organization with a specific status."""
        pass

    @abstractmethod
    async def find_by_idempotency_key(
        self,
        organization_id: OrganizationId,
        idempotency_key: WorkflowIdempotencyKey
    ) -> Optional[Workflow]:
        """Retrieves a Workflow by organization and its creation idempotency key."""
        pass

    @abstractmethod
    async def save(self, workflow: Workflow) -> None:
        """Saves/persists the Workflow aggregate root."""
        pass


from datetime import datetime
from .execution import WorkflowExecution, WorkflowExecutionId, WorkflowVersion

class IWorkflowExecutionRepository(ABC):
    """
    Interface/Port for WorkflowExecution repository.
    Handles persistence of execution records and outbox offsets.
    """

    @abstractmethod
    async def get_by_id(self, id: WorkflowExecutionId) -> Optional[WorkflowExecution]:
        """Retrieves a WorkflowExecution by ID."""
        pass

    @abstractmethod
    async def find_execution(
        self,
        workflow_id: WorkflowId,
        version: WorkflowVersion,
        source_event_id: str
    ) -> Optional[WorkflowExecution]:
        """Checks if a workflow version has already run for a specific source event."""
        pass

    @abstractmethod
    async def save(self, execution: WorkflowExecution) -> None:
        """Persists the WorkflowExecution aggregate."""
        pass

    @abstractmethod
    async def get_cursor(self, consumer_name: str) -> Optional[datetime]:
        """Retrieves the last processed event timestamp for the given consumer."""
        pass

    @abstractmethod
    async def save_cursor(self, consumer_name: str, last_event_created_at: datetime) -> None:
        """Updates the cursor checkpoint timestamp for the consumer."""
        pass

