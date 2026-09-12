from typing import Any, Dict
from ...domain.workflow.conditions import Condition, ConditionGroup, ConditionOperator, LogicalOperator


def get_payload_value(payload: Dict[str, Any], path: str) -> Any:
    """Helper to extract nested value from dictionary using dot notation (e.g. invoice.amount)."""
    parts = path.split(".")
    current = payload
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


class ConditionEvaluator:
    """
    Evaluator performing logical checks on event payloads without executing arbitrary code.
    Supports nested logical AND/OR groups.
    """

    def evaluate_group(self, group: ConditionGroup, payload: Dict[str, Any]) -> bool:
        if not group.conditions:
            return True

        results = []
        for cond in group.conditions:
            if isinstance(cond, Condition):
                results.append(self.evaluate_condition(cond, payload))
            elif isinstance(cond, ConditionGroup):
                results.append(self.evaluate_group(cond, payload))

        if group.operator == LogicalOperator.AND:
            return all(results)
        elif group.operator == LogicalOperator.OR:
            return any(results)
        return False

    def evaluate_condition(self, condition: Condition, payload: Dict[str, Any]) -> bool:
        val = get_payload_value(payload, condition.field)
        op = condition.operator
        target = condition.value

        if op == ConditionOperator.EXISTS:
            return val is not None

        if val is None:
            return False

        # Safe float conversions for numeric comparisons
        if op in (ConditionOperator.GREATER_THAN, ConditionOperator.LESS_THAN):
            try:
                val = float(val)
                target = float(target)
            except (ValueError, TypeError):
                return False

        if op == ConditionOperator.EQUALS:
            return str(val) == str(target)
        elif op == ConditionOperator.NOT_EQUALS:
            return str(val) != str(target)
        elif op == ConditionOperator.GREATER_THAN:
            return val > target
        elif op == ConditionOperator.LESS_THAN:
            return val < target
        return False
