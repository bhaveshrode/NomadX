import re
from typing import Any, Dict

class VariableResolver:
    """
    Safely resolves nested variables from the event context payload.
    Supports dot access syntax, e.g., 'event.invoice.amount' or 'invoice.amount'.
    """
    @staticmethod
    def resolve(path: str, context: dict) -> Any:
        if not path or not isinstance(context, dict):
            return None
        
        # Clean up path formatting
        path = path.strip("{} ").strip()
        
        # Normalize event or payload prefix paths
        if path.startswith("event."):
            path = path[6:]
        elif path.startswith("payload."):
            path = path[8:]
            
        parts = path.split(".")
        current = context
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current


class ParameterTemplater:
    """
    Interpolates variables enclosed in {{...}} within action parameters.
    Preserves type for exact single-variable templates (e.g. '{{amount}}' -> Decimal/float).
    """
    @staticmethod
    def resolve_template(val: Any, context: dict) -> Any:
        if isinstance(val, str):
            # Perfect single variable match to preserve original type
            match = re.match(r'^\{\{\s*(.*?)\s*\}\}$', val)
            if match:
                var_path = match.group(1)
                return VariableResolver.resolve(var_path, context)
            
            # String interpolation replacement
            def replace_var(m):
                path = m.group(1)
                res = VariableResolver.resolve(path, context)
                return str(res) if res is not None else ""
            
            return re.sub(r'\{\{\s*(.*?)\s*\}\}', replace_var, val)
            
        elif isinstance(val, dict):
            return {k: ParameterTemplater.resolve_template(v, context) for k, v in val.items()}
        elif isinstance(val, list):
            return [ParameterTemplater.resolve_template(item, context) for item in val]
        return val
