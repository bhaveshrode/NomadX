from typing import Any, Generic, TypeVar

ID = TypeVar("ID")

class Entity(Generic[ID]):
    """
    Base Entity class representing a domain object with a stable identity.
    Equality of entities is determined by their identity rather than their attributes.
    """
    def __init__(self, id: ID):
        if id is None:
            raise ValueError("Entity ID cannot be null or undefined.")
        self._id = id

    @property
    def id(self) -> ID:
        """Exposes the immutable identity of the entity."""
        return self._id

    def equals(self, other: Any) -> bool:
        """Checks if another entity is equal to this entity based on identity and class type."""
        if other is None:
            return False
        if not isinstance(other, Entity):
            return False
        if self is other:
            return True
        # Verify both entities belong to the exact same class type
        if type(self) is not type(other):
            return False

        this_id = self.id
        other_id = other.id

        if hasattr(this_id, "equals") and callable(getattr(this_id, "equals")):
            return this_id.equals(other_id)

        return this_id == other_id

    def get_hash_code(self) -> str:
        """Generates a stable hash code string consistent with the identity and equality."""
        class_name = self.__class__.__name__
        if hasattr(self._id, "value"):
            id_str = str(self._id.value)
        elif hasattr(self._id, "get_hash_code") and callable(getattr(self._id, "get_hash_code")):
            id_str = self._id.get_hash_code()
        else:
            id_str = str(self._id)
        return f"[Entity:{class_name}:{id_str}]"

    def __eq__(self, other: Any) -> bool:
        return self.equals(other)

    def __hash__(self) -> int:
        return hash(self.get_hash_code())
