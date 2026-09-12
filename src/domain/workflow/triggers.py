from ..shared.value_objects.value_object import ValueObject


class WorkflowTrigger(ValueObject):
    """Value object representing the triggering event condition for a workflow."""
    def __init__(self, event_type: str):
        if not event_type or not isinstance(event_type, str) or event_type.strip() == "":
            raise ValueError("Trigger event type cannot be empty.")
        super().__init__({"event_type": event_type.strip()})

    @property
    def event_type(self) -> str:
        return self.props["event_type"]

    def __str__(self) -> str:
        return self.event_type
