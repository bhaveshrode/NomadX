from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any

from ...domain.organization.value_objects import OrganizationId


class IReceivableQueryService(ABC):
    """
    Query service interface defining read-only financial reporting,
    dashboard aggregation, and customer statement calculations.
    """

    @abstractmethod
    async def get_customer_ar_summary(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        current_time: datetime
    ) -> Dict[str, Any]:
        """Retrieves outstanding AR balance summary for a customer."""
        pass

    @abstractmethod
    async def get_customer_statement(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        period_start: datetime,
        period_end: datetime,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Generates a reconciled customer statement of account for a period."""
        pass

    @abstractmethod
    async def get_customer_aging(
        self,
        organization_id: OrganizationId,
        customer_id: str,
        current_time: datetime
    ) -> Dict[str, Any]:
        """Retrieves outstanding balances grouped by aging buckets for a customer."""
        pass

    @abstractmethod
    async def get_org_ar_summary(
        self,
        organization_id: OrganizationId,
        current_time: datetime
    ) -> Dict[str, Any]:
        """Retrieves high-level AR dashboard metrics for an organization."""
        pass

    @abstractmethod
    async def get_outstanding_receivables(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves a list of all outstanding invoices/receivables with balances."""
        pass

    @abstractmethod
    async def get_receivable(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieves complete details of a single receivable."""
        pass

    @abstractmethod
    async def list_receivables(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        customer_reference: Optional[str] = None,
        invoice_reference: Optional[str] = None,
        aging_bucket: Optional[str] = None,
        dunning_stage: Optional[str] = None,
        overdue: Optional[bool] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        due_from: Optional[datetime] = None,
        due_to: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists organization receivables with pagination, sorting, and filters."""
        pass

    @abstractmethod
    async def get_customer_receivables(
        self,
        organization_id: OrganizationId,
        customer_reference: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves all receivables for a specific customer."""
        pass

    @abstractmethod
    async def get_allocations(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieves allocations history for a specific receivable."""
        pass

    @abstractmethod
    async def get_receivable_statement(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> Dict[str, Any]:
        """Generates a ledger statement for a single receivable."""
        pass

    @abstractmethod
    async def get_aging_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Retrieves organization-wide aging breakdown summary."""
        pass

    @abstractmethod
    async def get_dunning_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Retrieves organization-wide dunning stage breakdown summary."""
        pass

    @abstractmethod
    async def get_ar_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Retrieves detailed operational and performance metrics for the collection engine."""
        pass

    @abstractmethod
    async def get_ar_health(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Calculates system health diagnostics, detecting stuck events, jobs, or DLQs."""
        pass

    @abstractmethod
    async def get_ar_audit_logs(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Queries the audit log trail (transactional outbox events) for AR operations."""
        pass

    @abstractmethod
    async def get_receivable_timeline(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        """Generates a chronological lifecycle event timeline for a single receivable."""
        pass

    @abstractmethod
    async def get_collection_history(
        self,
        organization_id: OrganizationId,
        receivable_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieves full historical attempt records of collection actions on a receivable."""
        pass

