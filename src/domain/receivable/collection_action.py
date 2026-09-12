from datetime import datetime
from typing import Optional, Any
from ..shared.entities.entity import Entity
from ..shared.entities.identifier import UniqueEntityID
from ..organization.value_objects import OrganizationId
from .value_objects import ReceivableId


class CollectionActionId(UniqueEntityID):
    """
    Strongly typed identifier representing a CollectionAction's unique identity.
    """
    pass


class CollectionAction(Entity):
    """
    Domain Entity representing a scheduled collection/dunning retry action.
    """
    def __init__(self, id: CollectionActionId, props: dict):
        super().__init__(id)
        self._props = props

    @property
    def props(self) -> dict:
        return self._props

    @classmethod
    def create(
        cls,
        id: CollectionActionId,
        organization_id: OrganizationId,
        receivable_id: ReceivableId,
        action_type: str,
        scheduled_at: datetime,
        idempotency_key: str,
        status: str = "PENDING",
        attempt_count: int = 0,
        next_retry_at: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        processed_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        max_attempts: int = 3,
        dead_lettered_at: Optional[datetime] = None,
        failure_classification: Optional[str] = None
    ) -> "CollectionAction":
        now = created_at or datetime.utcnow()
        props = {
            "organization_id": organization_id,
            "receivable_id": receivable_id,
            "action_type": action_type,
            "scheduled_at": scheduled_at,
            "status": status,
            "attempt_count": attempt_count,
            "next_retry_at": next_retry_at,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "processed_at": processed_at,
            "error_message": error_message,
            "max_attempts": max_attempts,
            "dead_lettered_at": dead_lettered_at,
            "failure_classification": failure_classification
        }
        return cls(id, props)

    @property
    def organization_id(self) -> OrganizationId:
        return self.props["organization_id"]

    @property
    def receivable_id(self) -> ReceivableId:
        return self.props["receivable_id"]

    @property
    def action_type(self) -> str:
        return self.props["action_type"]

    @property
    def scheduled_at(self) -> datetime:
        return self.props["scheduled_at"]

    @property
    def status(self) -> str:
        return self.props["status"]

    @property
    def attempt_count(self) -> int:
        return self.props["attempt_count"]

    @property
    def next_retry_at(self) -> Optional[datetime]:
        return self.props["next_retry_at"]

    @property
    def idempotency_key(self) -> str:
        return self.props["idempotency_key"]

    @property
    def created_at(self) -> datetime:
        return self.props["created_at"]

    @property
    def processed_at(self) -> Optional[datetime]:
        return self.props["processed_at"]

    @property
    def error_message(self) -> Optional[str]:
        return self.props["error_message"]

    @property
    def max_attempts(self) -> int:
        return self.props.get("max_attempts", 3)

    @property
    def dead_lettered_at(self) -> Optional[datetime]:
        return self.props.get("dead_lettered_at")

    @property
    def failure_classification(self) -> Optional[str]:
        return self.props.get("failure_classification")

    def start_processing(self) -> None:
        """Transitions status to PROCESSING."""
        self.props["status"] = "PROCESSING"

    def complete(self, processed_at: datetime) -> None:
        """Transitions status to COMPLETED."""
        self.props["status"] = "COMPLETED"
        self.props["processed_at"] = processed_at
        self.props["error_message"] = None

    def fail(self, error_message: str, next_retry_at: Optional[datetime] = None, failure_classification: str = "TRANSIENT") -> None:
        """Transitions status to RETRYING or DEAD_LETTERED."""
        self.props["attempt_count"] += 1
        self.props["error_message"] = error_message
        self.props["failure_classification"] = failure_classification

        if failure_classification == "PERMANENT" or self.props["attempt_count"] >= self.max_attempts:
            self.props["status"] = "DEAD_LETTERED"
            self.props["dead_lettered_at"] = datetime.utcnow()
            self.props["next_retry_at"] = None
        else:
            self.props["status"] = "RETRYING"
            self.props["next_retry_at"] = next_retry_at

    def cancel(self) -> None:
        """Cancels a scheduled action."""
        self.props["status"] = "CANCELLED"
