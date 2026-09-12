from abc import ABC, abstractmethod
from typing import Generic, List, Optional, TypeVar
from ..entities.aggregate_root import AggregateRoot
from ..entities.identifier import Identifier
from ..specifications.specification import Specification

TAggregate = TypeVar("TAggregate", bound=AggregateRoot)
TId = TypeVar("TId", bound=Identifier)

class IReadRepository(Generic[TAggregate, TId], ABC):
    """
    Interface representing read-only persistence operations on an Aggregate Root.
    """
    @abstractmethod
    async def find_by_id(self, id: TId) -> Optional[TAggregate]:
        """
        Retrieves an aggregate by its unique identifier.
        Returns None if no aggregate is found.
        """
        pass

    @abstractmethod
    async def exists(self, id: TId) -> bool:
        """
        Checks if an aggregate with the given identifier exists.
        """
        pass

    @abstractmethod
    async def find_all(self) -> List[TAggregate]:
        """
        Retrieves all aggregates of this type.
        """
        pass

    @abstractmethod
    async def find_by_specification(
        self, specification: Specification[TAggregate]
    ) -> List[TAggregate]:
        """
        Retrieves all aggregates matching a given business specification.
        """
        pass


class IWriteRepository(Generic[TAggregate, TId], ABC):
    """
    Interface representing write-only persistence operations on an Aggregate Root.
    """
    @abstractmethod
    async def save(self, aggregate: TAggregate) -> None:
        """
        Saves or updates an aggregate in persistence.
        """
        pass

    @abstractmethod
    async def delete(self, id: TId) -> None:
        """
        Deletes an aggregate from persistence by its identifier.
        """
        pass


class IRepository(IReadRepository[TAggregate, TId], IWriteRepository[TAggregate, TId], ABC):
    """
    Composed Repository interface representing read and write operations.
    This is the standard repository contract that aggregates will define.
    """
    pass
