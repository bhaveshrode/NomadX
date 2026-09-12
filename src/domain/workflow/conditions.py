from enum import Enum
from typing import Any, List, Union
from ..shared.value_objects.value_object import ValueObject


class ConditionOperator(str, Enum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"
    IN = "IN"
    NOT_IN = "NOT_IN"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"


class Condition(ValueObject):
    """Value object representing a single conditional rule on a field."""
    def __init__(self, field: str, operator: ConditionOperator, value: Any = None):
        if not field or not isinstance(field, str) or field.strip() == "":
            raise ValueError("Condition field cannot be empty.")
        if not isinstance(operator, ConditionOperator):
            try:
                operator = ConditionOperator(operator)
            except ValueError:
                raise ValueError(f"Invalid condition operator: {operator}")

        # If operator is not EXISTS/NOT_EXISTS, a comparison value is required.
        if operator not in (ConditionOperator.EXISTS, ConditionOperator.NOT_EXISTS) and value is None:
            raise ValueError(f"Comparison value is required for operator '{operator}'.")

        super().__init__({
            "field": field.strip(),
            "operator": operator,
            "value": value
        })

    @property
    def field(self) -> str:
        return self.props["field"]

    @property
    def operator(self) -> ConditionOperator:
        return self.props["operator"]

    @property
    def value(self) -> Any:
        return self.props["value"]


class LogicalOperator(str, Enum):
    AND = "AND"
    OR = "OR"


class ConditionGroup(ValueObject):
    """Value object grouping multiple conditions with a logical operator (AND/OR)."""
    def __init__(self, operator: LogicalOperator, conditions: List[Union[Condition, 'ConditionGroup']]):
        if not isinstance(operator, LogicalOperator):
            try:
                operator = LogicalOperator(operator)
            except ValueError:
                raise ValueError(f"Invalid logical operator: {operator}")

        if not isinstance(conditions, list) or len(conditions) == 0:
            raise ValueError("Condition group must contain at least one condition or nested group.")

        for cond in conditions:
            if not isinstance(cond, (Condition, ConditionGroup)):
                raise ValueError("All items in condition group must be instances of Condition or ConditionGroup.")

        super().__init__({
            "operator": operator,
            "conditions": conditions
        })

    @property
    def operator(self) -> LogicalOperator:
        return self.props["operator"]

    @property
    def conditions(self) -> List[Union[Condition, 'ConditionGroup']]:
        return self.props["conditions"]
