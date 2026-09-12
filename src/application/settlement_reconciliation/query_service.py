from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any

from ...domain.organization.value_objects import OrganizationId


class ISettlementReconciliationQueryService(ABC):
    """
    Interface for read-side queries of the Settlement & Reconciliation bounded context.
    Optimized for multi-tenant query retrieval, sorting, filtering, and pagination.
    """
    @abstractmethod
    async def get_settlement(self, organization_id: OrganizationId, settlement_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve high-level detail about a settlement batch."""
        pass

    @abstractmethod
    async def list_settlements(
        self,
        organization_id: OrganizationId,
        reference: Optional[str] = None,
        status: Optional[str] = None,
        currency: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        merchant_reference: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List and filter settlement batches for an organization."""
        pass

    @abstractmethod
    async def get_settlement_entries(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        """Retrieve entry line items inside a settlement."""
        pass

    @abstractmethod
    async def get_settlement_adjustments(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        """Retrieve applied financial adjustments."""
        pass

    @abstractmethod
    async def get_settlement_discrepancies(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        """Retrieve logged reconciliation discrepancies."""
        pass

    @abstractmethod
    async def get_reconciliation_result(self, organization_id: OrganizationId, settlement_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve active verification/reconciliation status and descriptive summary."""
        pass

    @abstractmethod
    async def list_failed_settlements(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Retrieve failed or dead-lettered settlement batches."""
        pass

    @abstractmethod
    async def list_dead_letter_settlements(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Retrieve dead-lettered settlement batches."""
        pass

    @abstractmethod
    async def get_settlement_metrics(self, organization_id: OrganizationId) -> Dict[str, Any]:
        """Retrieve execution metrics and rates for dashboard visualizer."""
        pass

    @abstractmethod
    async def get_settlement_audit_logs(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Retrieve auditable trail logs for operations intervention."""
        pass

    @abstractmethod
    async def get_settlement_timeline(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        """Retrieve historical lifecycle timeline for a specific settlement batch."""
        pass

    @abstractmethod
    async def get_settlement_health(self, organization_id: OrganizationId) -> Dict[str, Any]:
        """Diagnose processing context and identify health alerts."""
        pass
