import re
import uuid
from typing import Any, TypeVar, Generic
from ..value_objects.value_object import ValueObject

T = TypeVar("T")

class Identifier(ValueObject, Generic[T]):
    """
    Base abstract Identifier class extending ValueObject.
    Provides strongly typed, immutable identities.
    """
    def __init__(self, value: T):
        if value is None:
            raise ValueError("Identifier value cannot be null or undefined.")
        if isinstance(value, str) and value.strip() == "":
            raise ValueError("Identifier value cannot be empty.")
        super().__init__({"value": value})

    @property
    def value(self) -> T:
        """Retrieves the raw primitive value of the identifier."""
        return self.props["value"]

    def __str__(self) -> str:
        return str(self.value)

    def to_json(self) -> T:
        """Serializes the identifier as its raw primitive value when converted to JSON."""
        return self.value


UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE
)

class UniqueEntityID(Identifier[str]):
    """
    Concrete UUID-backed Identifier.
    Automatically generates a new UUID v4 if no value is provided.
    """
    def __init__(self, value: Any = None):
        if value is None:
            actual_value = str(uuid.uuid4())
        elif isinstance(value, str):
            actual_value = value
        elif isinstance(value, dict) and "value" in value:
            actual_value = str(value["value"])
        elif hasattr(value, "value"):
            actual_value = str(value.value)
        else:
            actual_value = str(value)

        super().__init__(actual_value)

        if not UUID_REGEX.match(self.value):
            raise ValueError("Invalid UUID format.")

    @classmethod
    def generate(cls) -> "UniqueEntityID":
        """Factory method to generate a new UniqueEntityID."""
        return cls()

    @classmethod
    def from_str(cls, value: str) -> "UniqueEntityID":
        """Factory method to create a UniqueEntityID from an existing string."""
        return cls(value)
