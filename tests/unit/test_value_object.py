import pytest
from nomadx.src.domain.shared.value_objects.value_object import ValueObject

class Money(ValueObject):
    def __init__(self, amount: float, currency: str):
        super().__init__({"amount": amount, "currency": currency})

    @property
    def amount(self) -> float:
        return self.props["amount"]

    @property
    def currency(self) -> str:
        return self.props["currency"]

def test_value_object_equality():
    m1 = Money(100.0, "USD")
    m2 = Money(100.0, "USD")
    m3 = Money(150.0, "USD")
    m4 = Money(100.0, "EUR")

    assert m1.equals(m2)
    assert m1 == m2
    assert not m1.equals(m3)
    assert m1 != m3
    assert m1 != m4
    assert m1 is not m2

def test_value_object_immutability():
    m1 = Money(100.0, "USD")
    
    with pytest.raises(AttributeError):
        m1.amount = 200.0  # type: ignore

    with pytest.raises(TypeError):
        m1.props["amount"] = 200.0  # type: ignore

def test_value_object_hashing():
    m1 = Money(100.0, "USD")
    m2 = Money(100.0, "USD")
    m3 = Money(150.0, "USD")

    assert hash(m1) == hash(m2)
    assert hash(m1) != hash(m3)
    
    # Verify cached hash code
    h1 = m1.get_hash_code()
    h2 = m1.get_hash_code()
    assert h1 == h2
    assert ":Money:" in h1

def test_value_object_nested_freezing():
    class ComplexVO(ValueObject):
        pass

    c1 = ComplexVO({"list_val": [1, 2, {"a": "b"}], "dict_val": {"x": "y"}})
    
    # Assert converted to tuple and MappingProxyType
    assert isinstance(c1.props["list_val"], tuple)
    # Ah! In deep_freeze, if isinstance(val, dict) -> MappingProxyType. So c1.props["list_val"][2] is a MappingProxyType!
    from types import MappingProxyType
    assert isinstance(c1.props["list_val"][2], MappingProxyType)
    assert isinstance(c1.props["dict_val"], MappingProxyType)

    # Modify attempt raises error
    with pytest.raises(TypeError):
        c1.props["list_val"][0] = 10  # type: ignore
