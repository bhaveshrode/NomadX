from ..shared.specifications.specification import Specification
from .value_objects import WorkflowStatus


class WorkflowCanBeActivated(Specification["Workflow"]):
    """Specification verifying if a Workflow can transition to ACTIVE status."""
    def is_satisfied_by(self, candidate: "Workflow") -> bool:
        if candidate.status not in (WorkflowStatus.DRAFT, WorkflowStatus.PAUSED):
            return False
        # Must have definitions and an active/latest version
        if not candidate.definitions:
            return False
        
        # Check active definition validity
        active_ver = candidate.active_version_number or max(candidate.definitions.keys())
        active_def = candidate.definitions.get(active_ver)
        if not active_def:
            return False

        spec = WorkflowDefinitionIsValid()
        return spec.is_satisfied_by(active_def)


class WorkflowCanBePaused(Specification["Workflow"]):
    """Specification verifying if a Workflow can transition to PAUSED status."""
    def is_satisfied_by(self, candidate: "Workflow") -> bool:
        return candidate.status == WorkflowStatus.ACTIVE


class WorkflowCanBeArchived(Specification["Workflow"]):
    """Specification verifying if a Workflow can transition to ARCHIVED status."""
    def is_satisfied_by(self, candidate: "Workflow") -> bool:
        return candidate.status in (WorkflowStatus.ACTIVE, WorkflowStatus.PAUSED)


class WorkflowCanBeUpdated(Specification["Workflow"]):
    """Specification verifying if a Workflow can have its metadata or versions updated."""
    def is_satisfied_by(self, candidate: "Workflow") -> bool:
        return candidate.status != WorkflowStatus.ARCHIVED


class WorkflowHasValidTrigger(Specification["WorkflowDefinition"]):
    """Specification verifying if a WorkflowDefinition has a valid trigger."""
    def is_satisfied_by(self, candidate: "WorkflowDefinition") -> bool:
        return candidate.trigger is not None and candidate.trigger.event_type != ""


class WorkflowHasValidActions(Specification["WorkflowDefinition"]):
    """Specification verifying if a WorkflowDefinition has at least one action."""
    def is_satisfied_by(self, candidate: "WorkflowDefinition") -> bool:
        return candidate.actions is not None and len(candidate.actions) > 0


class WorkflowDefinitionIsValid(Specification["WorkflowDefinition"]):
    """Specification verifying if a WorkflowDefinition is fully valid to execute."""
    def is_satisfied_by(self, candidate: "WorkflowDefinition") -> bool:
        spec_trigger = WorkflowHasValidTrigger()
        spec_actions = WorkflowHasValidActions()
        return spec_trigger.is_satisfied_by(candidate) and spec_actions.is_satisfied_by(candidate)


class WorkflowVersionCanBeActivated(Specification["Workflow"]):
    """Specification verifying if a specific version of a Workflow can be activated."""
    def __init__(self, version_number: int):
        self.version_number = version_number

    def is_satisfied_by(self, candidate: "Workflow") -> bool:
        definition = candidate.definitions.get(self.version_number)
        if not definition:
            return False
        spec = WorkflowDefinitionIsValid()
        return spec.is_satisfied_by(definition)
