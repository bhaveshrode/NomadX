import pytest
from nomadx.src.domain.shared.entities.identifier import Identifier, UniqueEntityID

class CustomId(Identifier[int]):
    pass

def test_custom_identifier():
    cid1 = CustomId(123)
    cid2 = CustomId(123)
    cid3 = CustomId(456)

    assert cid1.value == 123
    assert str(cid1) == "123"
    assert cid1 == cid2
    assert cid1 != cid3
    assert cid1.to_json() == 123

def test_identifier_validation():
    with pytest.raises(ValueError):
        CustomId(None)  # type: ignore

    with pytest.raises(ValueError):
        Identifier("")  # type: ignore

def test_unique_entity_id_generation():
    ueid1 = UniqueEntityID()
    ueid2 = UniqueEntityID()
    
    assert ueid1.value != ueid2.value
    # Assert standard UUID length and format
    assert len(ueid1.value) == 36

def test_unique_entity_id_regex_validation():
    # Valid UUID
    ueid = UniqueEntityID("7560f6ff-93ea-4d12-beca-40879437cba7")
    assert ueid.value == "7560f6ff-93ea-4d12-beca-40879437cba7"

    # Invalid UUID
    with pytest.raises(ValueError, match="Invalid UUID format"):
        UniqueEntityID("invalid-uuid-string")
