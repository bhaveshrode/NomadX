from abc import ABC, abstractmethod
from typing import Dict, Any, List

from ...domain.organization.value_objects import OrganizationId
from ...presentation.api.dependencies.context import ExecutionContext


class IFinancialRecoveryService(ABC):
    """
    Interface for the controlled financial recovery and remediation layer.
    """

    @abstractmethod
    async def recheck_consistency(
        self,
        organization_id: OrganizationId,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Runs cross-context financial invariant checks, resolves fixed findings,
        updates health metrics, and records the run in the remediation audit log.
        """
        pass

    @abstractmethod
    async def retry_failed_event(
        self,
        organization_id: OrganizationId,
        event_id: str,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Retries a failed/dead-lettered outbox event within the organization context
        safely with idempotency and records the outcome.
        """
        pass

    @abstractmethod
    async def replay_event(
        self,
        organization_id: OrganizationId,
        event_id: str,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Replays a previously processed/eligible event to resolve downstream missing states,
        verifying target resources are not finalized or locked.
        """
        pass

    @abstractmethod
    async def recover_stuck_processing(
        self,
        organization_id: OrganizationId,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Safely identifies and clears stuck events or reconciliation batches in intermediate
        states, returning them to pending or dead-lettered states with auditing.
        """
        pass

    @abstractmethod
    async def get_remediation_history(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        """
        Retrieves the complete list of all remediation audit trail records for the organization.
        """
        pass
