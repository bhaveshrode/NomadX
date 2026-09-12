from datetime import datetime
from typing import List
from ..shared.value_objects.value_object import ValueObject
from .value_objects import WorkflowVersion
from .triggers import WorkflowTrigger
from .conditions import ConditionGroup
from .actions import WorkflowAction


class WorkflowDefinition(ValueObject):
    """Value object representing an immutable versioned definition of a workflow."""
    def __init__(
        self,
        version: WorkflowVersion,
        trigger: WorkflowTrigger,
        conditions: ConditionGroup,
        actions: List[WorkflowAction],
        created_at: datetime
    ):
        if not isinstance(version, WorkflowVersion):
            raise ValueError("version must be a WorkflowVersion instance.")
        if not isinstance(trigger, WorkflowTrigger):
            raise ValueError("trigger must be a WorkflowTrigger instance.")
        if not isinstance(conditions, ConditionGroup):
            raise ValueError("conditions must be a ConditionGroup instance.")
        if not isinstance(actions, list) or len(actions) == 0:
            raise ValueError("actions must be a non-empty list of WorkflowAction instances.")
        for action in actions:
            if not isinstance(action, WorkflowAction):
                raise ValueError("All actions must be WorkflowAction instances.")
        if not isinstance(created_at, datetime):
            raise ValueError("created_at must be a datetime instance.")

        super().__init__({
            "version": version,
            "trigger": trigger,
            "conditions": conditions,
            "actions": actions,
            "created_at": created_at
        })

    @property
    def version(self) -> WorkflowVersion:
        return self.props["version"]

    @property
    def trigger(self) -> WorkflowTrigger:
        return self.props["trigger"]

    @property
    def conditions(self) -> ConditionGroup:
        return self.props["conditions"]

    @property
    def actions(self) -> List[WorkflowAction]:
        return self.props["actions"]

    @property
    def created_at(self) -> datetime:
        return self.props["created_at"]
