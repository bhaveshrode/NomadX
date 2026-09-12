import re
from enum import Enum
from typing import Any
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject


class WorkflowId(UniqueEntityID):
    """Strongly typed identifier representing a Workflow's unique identity."""
    pass


class WorkflowReference(ValueObject):
    """Value object representing a unique merchant-facing workflow reference (e.g. WF-2026-000001)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Workflow reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^WF-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Workflow reference must start with 'WF-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class WorkflowName(ValueObject):
    """Value object representing a validated workflow name."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Workflow name cannot be empty.")
        clean_val = value.strip()
        if len(clean_val) > 100:
            raise ValueError("Workflow name cannot exceed 100 characters.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class WorkflowVersion(ValueObject):
    """Value object representing a workflow definition version."""
    def __init__(self, value: int):
        if not isinstance(value, int) or value < 1:
            raise ValueError("Workflow version must be a positive integer starting from 1.")
        super().__init__({"value": value})

    @property
    def value(self) -> int:
        return self.props["value"]

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, WorkflowVersion):
            return False
        return self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return str(self.value)


class WorkflowIdempotencyKey(ValueObject):
    """Value object representing a tenant-scoped workflow creation idempotency key."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Idempotency key cannot be empty.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value
