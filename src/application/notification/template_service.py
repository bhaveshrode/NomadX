import re
from typing import Dict, Any, Optional

from ...domain.organization.value_objects import OrganizationId
from ...domain.notification.template import TemplateReference, TemplateVersion
from ...domain.notification.repositories import INotificationTemplateRepository
from ...domain.shared.result.result import Result, ResultError


class NotificationTemplateService:
    """
    Application service responsible for resolving template versions,
    validating required parameters, and rendering content.
    """
    def __init__(self, template_repo: INotificationTemplateRepository) -> None:
        self._template_repo = template_repo

    async def render(
        self,
        organization_id: OrganizationId,
        reference: TemplateReference,
        variables: Dict[str, Any],
        version: Optional[TemplateVersion] = None
    ) -> Result[str]:
        # 1. Resolve template version
        if version:
            template = await self._template_repo.find_by_reference_and_version(
                organization_id, reference, version
            )
        else:
            template = await self._template_repo.get_active_version(
                organization_id, reference
            )

        if not template:
            return Result.fail(ResultError.not_found("Notification template version not found."))

        # 2. Extract and validate variables
        content_str = template.content.value
        # Matches {{variable_name}} placeholders
        placeholders = set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", content_str))

        for ph in placeholders:
            if ph not in variables:
                return Result.fail(ResultError.validation(f"Missing required template variable: {ph}"))

        # 3. Render content
        rendered = content_str
        for ph in placeholders:
            val_str = str(variables[ph])
            # Escape regex characters if placeholder is used directly or use simple string replace
            # since placeholder contains only alphanumeric/underscores, we can replace safely
            rendered = re.sub(r"\{\{\s*" + ph + r"\s*\}\}", val_str, rendered)

        return Result.ok(rendered)
