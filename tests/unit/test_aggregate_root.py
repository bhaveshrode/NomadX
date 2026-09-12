import pytest
from nomadx.src.domain.shared.entities.aggregate_root import AggregateRoot
from nomadx.src.domain.shared.entities.identifier import UniqueEntityID
from nomadx.src.domain.shared.events.domain_event import DomainEvent

class MockId(UniqueEntityID):
    pass

class MockEvent(DomainEvent):
    def __init__(self, aggregate_id: MockId):
        super().__init__()
        self.aggregate_id = aggregate_id

    def get_aggregate_id(self) -> str:
        return str(self.aggregate_id.value)

class MockAggregate(AggregateRoot[MockId]):
    def trigger_action(self):
        self._add_domain_event(MockEvent(self.id))

def test_aggregate_root_event_lifecycle():
    agg_id = MockId()
    agg = MockAggregate(agg_id)
    
    assert len(agg.domain_events) == 0

    agg.trigger_action()
    assert len(agg.domain_events) == 1
    assert isinstance(agg.domain_events[0], MockEvent)
    assert agg.domain_events[0].get_aggregate_id() == str(agg_id.value)

    # Immutability of domain event collection
    with pytest.raises(AttributeError):
        agg.domain_events.append(MockEvent(agg_id))  # type: ignore

    agg.clear_domain_events()
    assert len(agg.domain_events) == 0
