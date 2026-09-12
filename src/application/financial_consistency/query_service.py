from abc import ABC, abstractmethod
from typing import Dict, Any

from ...domain.organization.value_objects import OrganizationId


class IFinancialConsistencyQueryService(ABC):
    """
    Query service interface defining methods to execute cross-context financial consistency
    invariant checks and retrieve overall consistency health metrics.
    """

    @abstractmethod
    async def check_financial_consistency(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Executes cross-context financial invariant checks for an organization and
        returns a report summarizing the liveness check, healthy vs degraded counts,
        and details of any active violations.
        """
        pass

    @abstractmethod
    async def get_consistency_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Retrieves high-level performance and health metrics related to consistency
        checks (e.g. check counts, healthy check rate, active violation count).
        """
        pass

    @abstractmethod
    async def get_active_violations(
        self,
        organization_id: OrganizationId
    ) -> list[Dict[str, Any]]:
        """
        Retrieves all currently active consistency violations for the organization.
        """
        pass

    @abstractmethod
    async def get_violation_history(
        self,
        organization_id: OrganizationId
    ) -> list[Dict[str, Any]]:
        """
        Retrieves the complete historical log of all consistency violations for the organization.
        """
        pass

    @abstractmethod
    async def get_entity_timeline(
        self,
        organization_id: OrganizationId,
        entity_id: str
    ) -> list[Dict[str, Any]]:
        """
        Generates a consolidated, chronological timeline of domain/outbox events,
        processing actions, and consistency findings for a given financial entity.
        """
        pass
