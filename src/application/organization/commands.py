from dataclasses import dataclass
from typing import Dict, Any

from ...domain.shared.result.result import Result, ResultError
from ...domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
from ...domain.organization.organization import Organization
from ...domain.organization.repositories import IOrganizationRepository

@dataclass
class CreateOrganizationCommand:
    organization_id: str
    name: str
    slug: str
    owner_id: str
    default_currency: str


class CreateOrganizationHandler:
    """
    Handler executing the business use case of registering a new Organization.
    """
    def __init__(self, org_repo: IOrganizationRepository):
        self._org_repo = org_repo

    async def handle(self, command: CreateOrganizationCommand) -> Result[Dict[str, Any]]:
        try:
            # Construct domain Value Objects (validates inputs structurally)
            org_id = OrganizationId(command.organization_id)
            name = OrganizationName(command.name)
            slug = OrganizationSlug(command.slug)
            owner_id = UserId(command.owner_id)
            settings = OrganizationSettings(command.default_currency)
        except ValueError as ex:
            return Result.fail(ResultError.validation(str(ex)))

        # Business validation: Unique slug check
        existing_by_slug = await self._org_repo.find_by_slug(slug)
        if existing_by_slug is not None:
            return Result.fail(
                ResultError.conflict(f"Organization slug '{slug.value}' is already taken.")
            )

        # Business validation: Unique ID check
        existing_by_id = await self._org_repo.find_by_id(org_id)
        if existing_by_id is not None:
            return Result.fail(
                ResultError.conflict(f"Organization with ID '{org_id.value}' already exists.")
            )

        # Instantiate aggregate using factory (registers OrganizationCreated domain event)
        org_result = Organization.create(
            id=org_id,
            name=name,
            slug=slug,
            owner_id=owner_id,
            settings=settings,
        )
        if org_result.is_failure:
            return Result.fail(org_result.error)

        org = org_result.value

        # Persist aggregate and its raised domain events (within transaction)
        await self._org_repo.save(org)

        # Map saved aggregate to a plain DTO representation
        dto = {
            "id": org.id.value,
            "name": org.name.value,
            "slug": org.slug.value,
            "status": org.status.value,
            "default_currency": org.settings.default_currency,
            "owner_id": org.owner_id.value,
            "created_at": org.created_at.isoformat(),
            "updated_at": org.updated_at.isoformat(),
        }

        return Result.ok(dto)
