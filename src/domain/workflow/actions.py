from enum import Enum
from typing import Any, Dict
from ..shared.value_objects.value_object import ValueObject


class ActionType(str, Enum):
    SEND_NOTIFICATION = "SEND_NOTIFICATION"
    CREATE_SETTLEMENT = "CREATE_SETTLEMENT"


class WorkflowAction(ValueObject):
    """Value object representing an executable action triggered by a workflow."""
    def __init__(self, action_type: ActionType, parameters: Dict[str, Any]):
        if not isinstance(action_type, ActionType):
            try:
                action_type = ActionType(action_type)
            except ValueError:
                raise ValueError(f"Invalid action type: {action_type}")

        if not isinstance(parameters, dict):
            raise ValueError("Action parameters must be a dictionary.")

        # Specific action-parameter validation rules
        if action_type == ActionType.SEND_NOTIFICATION:
            if "template_id" not in parameters or not isinstance(parameters["template_id"], str) or parameters["template_id"].strip() == "":
                raise ValueError("template_id parameter is required and must be a non-empty string for SEND_NOTIFICATION action.")

        super().__init__({
            "action_type": action_type,
            "parameters": parameters
        })

    @property
    def action_type(self) -> ActionType:
        return self.props["action_type"]

    @property
    def parameters(self) -> Dict[str, Any]:
        return self.props["parameters"]
