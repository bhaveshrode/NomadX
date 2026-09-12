import pytest
from nomadx.src.domain.shared.entities.entity import Entity
from nomadx.src.domain.shared.entities.identifier import UniqueEntityID

class ProductId(UniqueEntityID):
    pass

class Product(Entity[ProductId]):
    def __init__(self, id: ProductId, name: str):
        super().__init__(id)
        self.name = name

def test_entity_equality():
    id1 = ProductId()
    id2 = ProductId()

    p1 = Product(id1, "Laptop")
    p2 = Product(id1, "Notebook")  # Same ID, different name
    p3 = Product(id2, "Laptop")    # Different ID, same name

    assert p1 == p2
    assert p1 != p3
    assert p1 is not p2

def test_entity_type_safety():
    class OtherEntity(Entity[ProductId]):
        pass

    id1 = ProductId()
    p1 = Product(id1, "Laptop")
    o1 = OtherEntity(id1)

    assert p1 != o1  # Different types, same ID

def test_entity_hashing():
    id1 = ProductId("7560f6ff-93ea-4d12-beca-40879437cba7")
    p1 = Product(id1, "Laptop")
    p2 = Product(id1, "Notebook")

    assert hash(p1) == hash(p2)
    assert "[Entity:Product:7560f6ff-93ea-4d12-beca-40879437cba7]" in p1.get_hash_code()
