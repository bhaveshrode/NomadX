from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from ...domain.organization.value_objects import OrganizationId


class IObservabilityQueryService(ABC):
    """
    Interface for querying structured telemetry, component health dashboards,
    and operational observability metrics within tenant boundaries.
    """
    @abstractmethod
    async def query_traces(
        self,
        organization_id: OrganizationId,
        component: Optional[str] = None,
        severity: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Queries structured operational traces for an organization with optional filters.
        """
        pass

    @abstractmethod
    async def get_trace_by_correlation_id(
        self,
        organization_id: OrganizationId,
        correlation_id: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all telemetry events sharing the same correlation ID, ordered chronologically.
        """
        pass

    @abstractmethod
    async def get_component_health(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Analyzes recent telemetry logs to determine the status (healthy, degraded, inactive)
        of workers, APIs, and workflows.
        """
        pass

    @abstractmethod
    async def get_telemetry_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Provides aggregated performance metrics such as error rates, request counts,
        and average execution durations.
        """
        pass

    @abstractmethod
    async def evaluate_health_and_readiness(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Evaluates current operational health, readiness, and dependencies based on multi-signal intelligence.
        Persists state transitions automatically if changes occur.
        """
        pass

    @abstractmethod
    async def get_health_transition_history(
        self,
        organization_id: OrganizationId,
        component: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves persisted history of health state transitions within organization context.
        """
        pass

    @abstractmethod
    async def run_anomaly_detection(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Triggers the deterministic and temporal anomaly detection scanning.
        """
        pass

    @abstractmethod
    async def query_anomalies(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Retrieves a filtered list of persisted anomalies within the tenant boundary.
        """
        pass

    @abstractmethod
    async def get_anomaly_by_id(
        self,
        organization_id: OrganizationId,
        anomaly_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves full details of a specific anomaly.
        """
        pass

    @abstractmethod
    async def update_anomaly_status(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        status: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates the lifecycle status of an anomaly (e.g. ACKNOWLEDGED, RESOLVED).
        """
        pass

    @abstractmethod
    async def assign_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        owner_id: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Assigns an exception to a specific operational owner and transitions status to ASSIGNED.
        """
        pass

    @abstractmethod
    async def mitigate_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        notes: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Logs mitigation steps and transitions status to MITIGATED.
        """
        pass

    @abstractmethod
    async def escalate_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        reason: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Escalates an exception, marking it escalated and increasing its priority.
        """
        pass

    @abstractmethod
    async def get_recurring_patterns(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        """
        Analyzes historical resolved and unresolved exceptions to identify recurring component issues.
        """
        pass

    @abstractmethod
    async def run_remediation_pipeline(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Scans active exceptions, evaluates safety criteria, selects strategies, runs recovery actions, and verifies state.
        """
        pass

    @abstractmethod
    async def retry_remediation(
        self,
        organization_id: OrganizationId,
        remediation_id: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Manually retries a failed remediation action.
        """
        pass

    @abstractmethod
    async def query_remediations(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Queries historical execution logs of automated remediations.
        """
        pass

    @abstractmethod
    async def evaluate_operational_situations(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Groups active exceptions into situations, calculating impact scores and mapping causality chains.
        """
        pass

    @abstractmethod
    async def query_situations(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Queries active or resolved operational situations.
        """
        pass

    @abstractmethod
    async def get_situation_by_id(
        self,
        organization_id: OrganizationId,
        situation_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves detailed information of an operational situation, including causality trace logs.
        """
        pass

    @abstractmethod
    async def get_control_plane_overview(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Consolidates platform health, readiness, situations, exceptions, violations, and active remediations.
        """
        pass

    @abstractmethod
    async def get_situation_timeline(
        self,
        organization_id: OrganizationId,
        situation_id: str
    ) -> List[Dict[str, Any]]:
        """
        Generates chronological trace sequence of telemetry, anomalies, exceptions, and remediations.
        """
        pass

    @abstractmethod
    async def query_audit_trails(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Queries historical admin intervention audit logs.
        """
        pass

    @abstractmethod
    async def log_control_plane_audit(
        self,
        organization_id: OrganizationId,
        actor_id: str,
        action: str,
        target_id: str,
        target_type: str,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        context: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> None:
        """
        Appends a record to the operational audit log.
        """
        pass

    @abstractmethod
    async def trigger_intelligence_tick(
        self,
        organization_id: OrganizationId,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates a single loop iteration: Observe -> Evaluate -> Detect -> Correlate -> Act -> Verify -> Re-evaluate.
        """
        pass

    @abstractmethod
    async def get_readiness_policy(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """
        Retrieves the tenant's autonomous readiness policy configuration.
        """
        pass

    @abstractmethod
    async def update_readiness_policy(
        self,
        organization_id: OrganizationId,
        readiness_level: str,
        emergency_disable: bool,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates the tenant's autonomous readiness policy configuration.
        """
        pass

    @abstractmethod
    async def cleanup_old_telemetry(
        self,
        organization_id: OrganizationId,
        older_than_days: int,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Deletes telemetry events and component health transitions older than specified days.
        """
        pass
