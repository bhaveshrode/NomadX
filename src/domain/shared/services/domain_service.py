from abc import ABC

class DomainService(ABC):
    """
    Base abstract class representing a Domain Service.
    Domain Services are stateless operations that encapsulate business logic
    that does not naturally belong to a single Entity or Value Object.

    Domain Services must remain strictly stateless and have no infrastructure dependencies.
    """
    def __init__(self) -> None:
        pass
