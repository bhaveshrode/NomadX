from typing import Any, Dict
from ..shared.entities.identifier import UniqueEntityID, Identifier
from ..shared.value_objects.value_object import ValueObject

class UserId(UniqueEntityID):
    """Identifier representing a User's identity."""
    pass

class OrganizationId(UniqueEntityID):
    """Identifier representing an Organization's identity."""
    pass

class OrganizationName(ValueObject):
    """Value object representing an Organization's name."""
    def __init__(self, value: str):
        if not value or value.strip() == "":
            raise ValueError("Organization name cannot be empty.")
        if len(value) < 3 or len(value) > 100:
            raise ValueError("Organization name must be between 3 and 100 characters.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class OrganizationSlug(ValueObject):
    """Value object representing an Organization's unique slug."""
    def __init__(self, value: str):
        if not value or value.strip() == "":
            raise ValueError("Organization slug cannot be empty.")
        clean_slug = value.strip().lower()
        # Slug format check (lowercase, numbers, and hyphens only)
        import re
        if not re.match(r"^[a-z0-9-]+$", clean_slug):
            raise ValueError("Organization slug can only contain lowercase letters, numbers, and hyphens.")
        if len(clean_slug) < 3 or len(clean_slug) > 50:
            raise ValueError("Organization slug must be between 3 and 50 characters.")
        super().__init__({"value": clean_slug})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class OrganizationSettings(ValueObject):
    """Value object representing Organization-wide preferences."""
    def __init__(self, default_currency: str):
        if not default_currency or default_currency.strip() == "":
            raise ValueError("Default currency cannot be empty.")
        clean_currency = default_currency.strip().upper()
        if len(clean_currency) < 3 or len(clean_currency) > 6:
            raise ValueError("Default currency must be a 3- to 6-letter ticker code.")
        super().__init__({"default_currency": clean_currency})

    @property
    def default_currency(self) -> str:
        return self.props["default_currency"]
