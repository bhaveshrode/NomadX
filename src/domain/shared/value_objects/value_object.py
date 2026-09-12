from types import MappingProxyType
from typing import Any, Dict

def deep_freeze(val: Any) -> Any:
    """Recursively converts dictionaries to MappingProxyType and sequences to tuples."""
    if isinstance(val, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple, set)):
        return tuple(deep_freeze(item) for item in val)
    return val

class ValueObject:
    """
    Base ValueObject class representing a domain primitive with no identity.
    Two value objects are equal if their structures and values are identical.
    Value objects are strictly immutable.
    """
    def __init__(self, props: Dict[str, Any]):
        if props is None:
            raise ValueError("ValueObject properties cannot be null or undefined.")
        # Enforce absolute immutability by deep freezing properties
        object.__setattr__(self, "_props", deep_freeze(dict(props)))
        object.__setattr__(self, "_cached_hash_code", None)

    @property
    def props(self) -> Dict[str, Any]:
        return self._props

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ValueObject is immutable and cannot be modified after instantiation.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ValueObject is immutable and cannot be modified after instantiation.")

    def equals(self, other: Any) -> bool:
        """Checks structural equality with another ValueObject of the same type."""
        if other is None:
            return False
        if not isinstance(other, ValueObject):
            return False
        # Enforce exact type safety
        if type(self) is not type(other):
            return False
        return self._deep_equals(self.props, other.props)

    def _deep_equals(self, a: Any, b: Any) -> bool:
        if a is b:
            return True
        if hasattr(a, "equals") and callable(getattr(a, "equals")):
            return a.equals(b)
        if hasattr(b, "equals") and callable(getattr(b, "equals")):
            return b.equals(a)
        
        if isinstance(a, (dict, MappingProxyType)) and isinstance(b, (dict, MappingProxyType)):
            if len(a) != len(b):
                return False
            for k in a:
                if k not in b:
                    return False
                if not self._deep_equals(a[k], b[k]):
                    return False
            return True

        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return False
            for val_a, val_b in zip(a, b):
                if not self._deep_equals(val_a, val_b):
                    return False
            return True

        return a == b

    def get_hash_code(self) -> str:
        """Generates a stable, deterministic hash string representing the values."""
        if self._cached_hash_code is None:
            h_str = self._generate_hash(self.props)
            object.__setattr__(
                self, 
                "_cached_hash_code", 
                f"[ValueObject:{self.__class__.__name__}:{h_str}]"
            )
        return self._cached_hash_code

    def _generate_hash(self, val: Any) -> str:
        if val is None:
            return "null"
        if hasattr(val, "get_hash_code") and callable(getattr(val, "get_hash_code")):
            return val.get_hash_code()
        if isinstance(val, (dict, MappingProxyType)):
            sorted_keys = sorted(val.keys())
            parts = [f"{k}:{self._generate_hash(val[k])}" for k in sorted_keys]
            return f"{{{','.join(parts)}}}"
        if isinstance(val, (list, tuple)):
            return f"[{','.join(self._generate_hash(v) for v in val)}]"
        return str(val)

    def __eq__(self, other: Any) -> bool:
        return self.equals(other)

    def __hash__(self) -> int:
        return hash(self.get_hash_code())

    def to_json(self) -> Any:
        """Serializes properties to a plain JSON representation."""
        return self._serialize(self.props)

    def _serialize(self, val: Any) -> Any:
        if val is None:
            return None
        if hasattr(val, "to_json") and callable(getattr(val, "to_json")):
            return val.to_json()
        if isinstance(val, (dict, MappingProxyType)):
            return {k: self._serialize(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)):
            return [self._serialize(v) for v in val]
        return val
