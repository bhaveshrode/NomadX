from abc import ABC, abstractmethod
from typing import Optional
from ..shared.repositories.repository import IRepository
from .user import User
from .value_objects import UserId, Email

class IUserRepository(IRepository[User, UserId], ABC):
    """
    Domain Repository Contract for User Aggregate.
    """
    @abstractmethod
    async def find_by_email(self, email: Email) -> Optional[User]:
        """
        Retrieves a user by their unique email address.
        Returns None if not found.
        """
        pass
