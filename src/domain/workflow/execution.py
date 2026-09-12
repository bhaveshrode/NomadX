from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.entities.identifier import UniqueEntityID
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..shared.events.domain_event import DomainEvent
from .value_objects import WorkflowId, WorkflowVersion


class WorkflowExecutionId(UniqueEntityID):
    """Unique identifier for a workflow execution instance."""
    pass


class WorkflowExecutionStarted(DomainEvent):
    """Event emitted when a workflow execution begins."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecutionCompleted(DomainEvent):
    """Event emitted when a workflow execution completes successfully."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecutionFailed(DomainEvent):
    """Event emitted when a workflow execution fails."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        reason: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.reason = reason

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecutionRetrying(DomainEvent):
    """Event emitted when a workflow execution encounters a retryable failure."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        retry_count: int,
        next_retry_at: datetime,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.retry_count = retry_count
        self.next_retry_at = next_retry_at

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecutionDeadLettered(DomainEvent):
    """Event emitted when a workflow execution exhausts its retry policy."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        reason: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.reason = reason

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecutionReplayed(DomainEvent):
    """Event emitted when a workflow execution is manually replayed/redrived."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.workflow_id = workflow_id

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowActionExecuted(DomainEvent):
    """Event emitted when an individual action inside the execution completes."""
    def __init__(
        self,
        execution_id: WorkflowExecutionId,
        action_type: str,
        status: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.execution_id = execution_id
        self.action_type = action_type
        self.status = status

    def get_aggregate_id(self) -> str:
        return str(self.execution_id.value)


class WorkflowExecution(AggregateRoot[WorkflowExecutionId]):
    """
    WorkflowExecution aggregate root tracking the lifecycle of an executed workflow definition
    triggered by a specific outbox event.
    """
    def __init__(self, id: WorkflowExecutionId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def workflow_id(self) -> WorkflowId:
        return self._props["workflow_id"]

    @property
    def version(self) -> WorkflowVersion:
        return self._props["version"]

    @property
    def source_event_id(self) -> str:
        return self._props["source_event_id"]

    @property
    def source_event_type(self) -> str:
        return self._props["source_event_type"]

    @property
    def status(self) -> str:
        return self._props["status"]

    @property
    def started_at(self) -> datetime:
        return self._props["started_at"]

    @property
    def completed_at(self) -> Optional[datetime]:
        return self._props.get("completed_at")

    @property
    def failure_reason(self) -> Optional[str]:
        return self._props.get("failure_reason")

    @property
    def action_results(self) -> List[Dict[str, Any]]:
        return self._props["action_results"]

    @property
    def retry_count(self) -> int:
        return self._props.get("retry_count", 0)

    @property
    def max_attempts(self) -> int:
        return self._props.get("max_attempts", 3)

    @property
    def next_retry_at(self) -> Optional[datetime]:
        return self._props.get("next_retry_at")

    @property
    def failure_classification(self) -> Optional[str]:
        return self._props.get("failure_classification")

    @property
    def dead_lettered_at(self) -> Optional[datetime]:
        return self._props.get("dead_lettered_at")

    @classmethod
    def start(
        cls,
        id: WorkflowExecutionId,
        workflow_id: WorkflowId,
        version: WorkflowVersion,
        source_event_id: str,
        source_event_type: str,
        max_attempts: int = 3
    ) -> Result["WorkflowExecution"]:
        """Starts a new execution tracking aggregate."""
        now = Clock.now()
        props = {
            "workflow_id": workflow_id,
            "version": version,
            "source_event_id": source_event_id,
            "source_event_type": source_event_type,
            "status": "STARTED",
            "started_at": now,
            "completed_at": None,
            "failure_reason": None,
            "action_results": [],
            "retry_count": 0,
            "max_attempts": max_attempts,
            "next_retry_at": None,
            "failure_classification": None,
            "dead_lettered_at": None
        }
        instance = cls(id, props)
        instance._add_domain_event(WorkflowExecutionStarted(id, workflow_id, now))
        return Result.ok(instance)

    def complete(self) -> None:
        """Transitions execution status to COMPLETED."""
        now = Clock.now()
        self._props["status"] = "COMPLETED"
        self._props["completed_at"] = now
        self._props["next_retry_at"] = None
        self._add_domain_event(WorkflowExecutionCompleted(self.id, self.workflow_id, now))

    def fail(self, reason: str, is_retryable: bool = False, backoff_duration: Optional[timedelta] = None) -> None:
        """Handles failure mapping: transitions to FAILED or SCHEDULES a retry, or DEAD_LETTER."""
        now = Clock.now()
        
        if is_retryable:
            next_attempt = self.retry_count + 1
            if next_attempt >= self.max_attempts:
                # Exhausted
                self.dead_letter(reason)
            else:
                # Schedule retry
                self._props["retry_count"] = next_attempt
                self._props["status"] = "RETRYING"
                # Default exponential backoff fallback if not specified: Attempt 1 -> 1m, Attempt 2 -> 5m, Attempt 3 -> 15m
                if not backoff_duration:
                    minutes = 1 if next_attempt == 1 else (5 if next_attempt == 2 else 15)
                    backoff_duration = timedelta(minutes=minutes)
                
                next_retry = now + backoff_duration
                self._props["next_retry_at"] = next_retry
                self._props["failure_classification"] = "RETRYABLE"
                self._props["failure_reason"] = reason
                self._add_domain_event(WorkflowExecutionRetrying(self.id, self.workflow_id, next_attempt, next_retry, now))
        else:
            # Permanent Failure -> transition straight to DEAD_LETTERED
            self.dead_letter(reason)

    def dead_letter(self, reason: str) -> None:
        """Permanently dead-letters the execution."""
        now = Clock.now()
        self._props["status"] = "DEAD_LETTERED"
        self._props["completed_at"] = now
        self._props["dead_lettered_at"] = now
        self._props["failure_classification"] = "PERMANENT"
        self._props["failure_reason"] = reason
        self._props["next_retry_at"] = None
        self._add_domain_event(WorkflowExecutionDeadLettered(self.id, self.workflow_id, reason, now))

    def replay(self) -> None:
        """Manually redrives/replays this execution context."""
        now = Clock.now()
        self._add_domain_event(WorkflowExecutionReplayed(self.id, self.workflow_id, now))

    def record_action_result(self, action_type: str, status: str, details: Optional[str] = None, duration: float = 0.0) -> None:
        """Appends individual action execution metrics."""
        now = Clock.now()
        self._props["action_results"].append({
            "action_type": action_type,
            "status": status,
            "details": details,
            "duration": duration,
            "executed_at": now.isoformat()
        })
        self._add_domain_event(WorkflowActionExecuted(self.id, action_type, status, now))
