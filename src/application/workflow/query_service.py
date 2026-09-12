from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from ...domain.organization.value_objects import OrganizationId


class IWorkflowQueryService(ABC):
    """
    Query service interface defining read-only workflow reporting,
    listing with filtering, and version details.
    """

    @abstractmethod
    async def list_workflows(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        name: Optional[str] = None,
        reference: Optional[str] = None,
        trigger: Optional[str] = None,
        active_version: Optional[int] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Lists workflows belonging to an organization with filters."""
        pass

    @abstractmethod
    async def get_workflow(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a single workflow's details."""
        pass

    @abstractmethod
    async def list_versions(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> List[Dict[str, Any]]:
        """Lists all definitions/versions configured for a workflow."""
        pass

    @abstractmethod
    async def get_version(
        self,
        organization_id: OrganizationId,
        workflow_id: str,
        version: int
    ) -> Optional[Dict[str, Any]]:
        """Retrieves details of a specific workflow definition version."""
        pass

    @abstractmethod
    async def list_executions(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        workflow_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Lists executions belonging to an organization with status/workflow filters."""
        pass

    @abstractmethod
    async def get_execution(
        self,
        organization_id: OrganizationId,
        execution_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieves details of a single workflow execution."""
        pass

    @abstractmethod
    async def get_workflow_metrics(
        self,
        organization_id: OrganizationId,
        workflow_id: str
    ) -> Dict[str, Any]:
        """Calculates operational and aggregate execution metrics for a workflow."""
        pass
