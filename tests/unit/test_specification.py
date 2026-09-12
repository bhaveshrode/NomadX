import pytest
from nomadx.src.domain.shared.specifications.specification import Specification

class IsEven(Specification[int]):
    def is_satisfied_by(self, candidate: int) -> bool:
        return candidate % 2 == 0

class IsPositive(Specification[int]):
    def is_satisfied_by(self, candidate: int) -> bool:
        return candidate > 0

def test_specification_composition():
    is_even = IsEven()
    is_positive = IsPositive()

    # AND operator (&)
    even_and_positive = is_even & is_positive
    assert even_and_positive.is_satisfied_by(4)
    assert not even_and_positive.is_satisfied_by(3)
    assert not even_and_positive.is_satisfied_by(-2)

    # OR operator (|)
    even_or_positive = is_even | is_positive
    assert even_or_positive.is_satisfied_by(3)   # Positive
    assert even_or_positive.is_satisfied_by(-2)  # Even
    assert not even_or_positive.is_satisfied_by(-3)

    # NOT operator (~)
    not_even = ~is_even
    assert not_even.is_satisfied_by(3)
    assert not not_even.is_satisfied_by(4)
