from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")

class Specification(ABC, Generic[T]):
    """
    Base abstract Specification class representing a business predicate.
    Can be logically composed using bitwise operators &, |, and ~.
    """
    @abstractmethod
    def is_satisfied_by(self, candidate: T) -> bool:
        """Checks if the candidate object satisfies the specification."""
        pass

    def __and__(self, other: "Specification[T]") -> "Specification[T]":
        return AndSpecification(self, other)

    def __or__(self, other: "Specification[T]") -> "Specification[T]":
        return OrSpecification(self, other)

    def __invert__(self) -> "Specification[T]":
        return NotSpecification(self)


class AndSpecification(Specification[T]):
    """Composite specification representing a logical AND of two specifications."""
    def __init__(self, left: Specification[T], right: Specification[T]):
        if not left or not right:
            raise ValueError("Both left and right specifications must be provided.")
        self.left = left
        self.right = right

    def is_satisfied_by(self, candidate: T) -> bool:
        return self.left.is_satisfied_by(candidate) and self.right.is_satisfied_by(candidate)


class OrSpecification(Specification[T]):
    """Composite specification representing a logical OR of two specifications."""
    def __init__(self, left: Specification[T], right: Specification[T]):
        if not left or not right:
            raise ValueError("Both left and right specifications must be provided.")
        self.left = left
        self.right = right

    def is_satisfied_by(self, candidate: T) -> bool:
        return self.left.is_satisfied_by(candidate) or self.right.is_satisfied_by(candidate)


class NotSpecification(Specification[T]):
    """Composite specification representing a logical NOT of a specification."""
    def __init__(self, spec: Specification[T]):
        if not spec:
            raise ValueError("Specification must be provided.")
        self.spec = spec

    def is_satisfied_by(self, candidate: T) -> bool:
        return not self.spec.is_satisfied_by(candidate)
