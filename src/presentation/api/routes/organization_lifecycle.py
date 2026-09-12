from uuid import uuid4
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import OrganizationDbModel

router = APIRouter(prefix="/api/organization", tags=["organization-lifecycle"])

class OrgSettingsDTO(BaseModel):
    organization_id: str
    name: str
    slug: str
    default_currency: str
    status: str
    webhook_url: Optional[str] = None
    notification_email: Optional[str] = None
    auto_dunning_enabled: bool = True

class UpdateOrgSettingsRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    default_currency: Optional[str] = Field(None, min_length=3, max_length=6)
    webhook_url: Optional[str] = Field(None)
    notification_email: Optional[str] = Field(None)
    auto_dunning_enabled: Optional[bool] = Field(None)

_org_extra_config = {}

@router.get("/settings", response_model=OrgSettingsDTO)
async def get_org_settings(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    Get organization configuration settings.
    """
    async with session_manager.session() as session:
        stmt = select(OrganizationDbModel).where(OrganizationDbModel.id == ctx.organization_id)
        res = await session.execute(stmt)
        org = res.scalar_one_or_none()

        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")

        extra = _org_extra_config.get(ctx.organization_id, {})
        return OrgSettingsDTO(
            organization_id=org.id,
            name=org.name,
            slug=org.slug,
            default_currency=org.default_currency,
            status=org.status,
            webhook_url=extra.get("webhook_url", "https://api.example.com/webhooks"),
            notification_email=extra.get("notification_email", f"admin@{org.slug}.com"),
            auto_dunning_enabled=extra.get("auto_dunning_enabled", True)
        )

@router.put("/settings", response_model=OrgSettingsDTO)
async def update_org_settings(
    payload: UpdateOrgSettingsRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Update organization configuration settings.
    """
    async with session_manager.session() as session:
        stmt = select(OrganizationDbModel).where(OrganizationDbModel.id == ctx.organization_id)
        res = await session.execute(stmt)
        org = res.scalar_one_or_none()

        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")

        if payload.name:
            org.name = payload.name
        if payload.default_currency:
            org.default_currency = payload.default_currency

        extra = _org_extra_config.setdefault(ctx.organization_id, {})
        if payload.webhook_url is not None:
            extra["webhook_url"] = payload.webhook_url
        if payload.notification_email is not None:
            extra["notification_email"] = payload.notification_email
        if payload.auto_dunning_enabled is not None:
            extra["auto_dunning_enabled"] = payload.auto_dunning_enabled

        await session.commit()

        return OrgSettingsDTO(
            organization_id=org.id,
            name=org.name,
            slug=org.slug,
            default_currency=org.default_currency,
            status=org.status,
            webhook_url=extra.get("webhook_url", "https://api.example.com/webhooks"),
            notification_email=extra.get("notification_email", f"admin@{org.slug}.com"),
            auto_dunning_enabled=extra.get("auto_dunning_enabled", True)
        )

@router.post("/deactivate")
async def deactivate_organization(
    ctx: ExecutionContext = Depends(require_roles(["OWNER"]))
):
    """
    Deactivates an organization profile. Requires OWNER role.
    """
    async with session_manager.session() as session:
        stmt = select(OrganizationDbModel).where(OrganizationDbModel.id == ctx.organization_id)
        res = await session.execute(stmt)
        org = res.scalar_one_or_none()

        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")

        org.status = "DEACTIVATED"
        await session.commit()

        return {
            "status": "success",
            "message": f"Organization '{org.name}' ({org.id}) has been deactivated."
        }
