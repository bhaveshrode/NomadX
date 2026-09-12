from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class CreateWorkflowCommand:
    """Command to construct a new Workflow in DRAFT status."""
    organization_id: str
    reference: str
    name: str
    description: Optional[str]
    idempotency_key: str
    trigger_event_type: Optional[str] = None
    conditions_data: Optional[dict] = None
    actions_data: Optional[List[dict]] = None


@dataclass(frozen=True)
class CreateWorkflowVersionCommand:
    """Command to append a new immutable definition version to a workflow."""
    workflow_id: str
    organization_id: str
    trigger_event_type: str
    conditions_data: dict
    actions_data: List[dict]


@dataclass(frozen=True)
class ActivateWorkflowCommand:
    """Command to activate a draft or paused workflow."""
    workflow_id: str
    organization_id: str


@dataclass(frozen=True)
class PauseWorkflowCommand:
    """Command to pause an active workflow."""
    workflow_id: str
    organization_id: str


@dataclass(frozen=True)
class ArchiveWorkflowCommand:
    """Command to permanently archive an active or paused workflow."""
    workflow_id: str
    organization_id: str


@dataclass(frozen=True)
class ActivateWorkflowVersionCommand:
    """Command to set a specific definition version as active."""
    workflow_id: str
    organization_id: str
    version_number: int
