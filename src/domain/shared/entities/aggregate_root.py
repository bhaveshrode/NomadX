from typing import List, Tuple, TypeVar
from .entity import Entity
from ..events.domain_event import DomainEvent

ID = TypeVar("ID")

class AggregateRoot(Entity[ID]):
    """
    Base AggregateRoot class that extends Entity.
    Serves as the consistency boundary for a cluster of domain objects.
    Manages the generation, retrieval, and clearing of domain events.
    """
    def __init__(self, id: ID):
        super().__init__(id)
        self._domain_events: List[DomainEvent] = []

    @property
    def domain_events(self) -> Tuple[DomainEvent, ...]:
        """Retrieves a read-only list of pending domain events for this aggregate."""
        return tuple(self._domain_events)

    def _add_domain_event(self, event: DomainEvent) -> None:
        """Registers a new domain event. Should only be called internally by business methods."""
        if event is None:
            raise ValueError("Domain event cannot be null or undefined.")
        self._domain_events.append(event)

    def clear_domain_events(self) -> None:
        """Clears the collection of pending domain events."""
        self._domain_events.clear()
