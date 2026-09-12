import re
from enum import Enum
from datetime import datetime
from typing import Dict, Any, Optional

from ..shared.entities.identifier import UniqueEntityID
from ..shared.entities.entity import Entity
from ..shared.value_objects.value_object import ValueObject
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId


class TemplateId(UniqueEntityID):
    """Strongly typed identifier representing a template's unique identity."""
    pass


class TemplateReference(ValueObject):
    """Value object representing a template's reference name (e.g. invoice.payment_received)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Template reference cannot be empty.")
        clean = value.strip()
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", clean):
            raise ValueError("Template reference must contain only alphanumeric characters, dots, hyphens, and underscores.")
        super().__init__({"value": clean})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class TemplateVersion(ValueObject):
    """Value object representing a template version string (e.g. v1, v2)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Template version cannot be empty.")
        clean = value.strip()
        if not re.match(r"^v\d+$", clean):
            raise ValueError("Template version must be in the format v[number] (e.g., v1).")
        super().__init__({"value": clean})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class TemplateContent(ValueObject):
    """Value object wrapping a template's text/HTML content."""
    def __init__(self, value: str):
        if not isinstance(value, str):
            raise ValueError("Template content must be a string.")
        super().__init__({"value": value})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class TemplateStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class NotificationTemplate(Entity):
    """
    NotificationTemplate Aggregate.
    Represents reusable, versioned templates mapped per organization tenant.
    """
    def __init__(self, id: TemplateId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def reference(self) -> TemplateReference:
        return self._props["reference"]

    @property
    def version(self) -> TemplateVersion:
        return self._props["version"]

    @property
    def content(self) -> TemplateContent:
        return self._props["content"]

    @property
    def status(self) -> TemplateStatus:
        return self._props["status"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: TemplateId,
        organization_id: OrganizationId,
        reference: TemplateReference,
        version: TemplateVersion,
        content: TemplateContent,
        status: TemplateStatus = TemplateStatus.INACTIVE,
        occurred_on: Optional[datetime] = None
    ) -> Result["NotificationTemplate"]:
        if None in (id, organization_id, reference, version, content):
            return Result.fail(ResultError.validation("All template properties are mandatory."))
        
        now = Clock.now()
        props = {
            "organization_id": organization_id,
            "reference": reference,
            "version": version,
            "content": content,
            "status": status,
            "created_at": occurred_on or now,
            "updated_at": occurred_on or now
        }
        return Result.ok(cls(id, props))

    def activate(self) -> Result[None]:
        if self.status == TemplateStatus.ARCHIVED:
            return Result.fail(ResultError.validation("Cannot activate an archived template."))
        self._props["status"] = TemplateStatus.ACTIVE
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)

    def deactivate(self) -> Result[None]:
        if self.status == TemplateStatus.ARCHIVED:
            return Result.fail(ResultError.validation("Cannot deactivate an archived template."))
        self._props["status"] = TemplateStatus.INACTIVE
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)

    def archive(self) -> Result[None]:
        self._props["status"] = TemplateStatus.ARCHIVED
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)

    def update_content(self, new_content: TemplateContent) -> Result[None]:
        if self.status == TemplateStatus.ARCHIVED:
            return Result.fail(ResultError.validation("Cannot update an archived template content."))
        self._props["content"] = new_content
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)
