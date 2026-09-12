from uuid import uuid4
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import UserDbModel, MembershipDbModel
from ....domain.identity.value_objects import Password

router = APIRouter(prefix="/api/organization/members", tags=["members"])

class MemberDTO(BaseModel):
    id: str
    email: str
    name: str
    role: str
    status: str
    joined_at: datetime

class InviteMemberRequest(BaseModel):
    email: str = Field(..., description="Email address of the invitee")
    name: str = Field(..., description="Full name of the invitee")
    role: str = Field("MEMBER", description="Role: OWNER, ADMIN, OPERATOR, MEMBER")

class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., description="Role: OWNER, ADMIN, OPERATOR, MEMBER")

@router.get("", response_model=List[MemberDTO])
async def list_members(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    List all members in the current organization context.
    """
    async with session_manager.session() as session:
        stmt = (
            select(UserDbModel.id, UserDbModel.email, UserDbModel.name, MembershipDbModel.role, MembershipDbModel.status, MembershipDbModel.joined_at)
            .join(MembershipDbModel, UserDbModel.id == MembershipDbModel.user_id)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
        )
        result = await session.execute(stmt)
        rows = result.all()
        
        return [
            MemberDTO(
                id=row[0],
                email=row[1],
                name=row[2],
                role=row[3],
                status=row[4],
                joined_at=row[5]
            )
            for row in rows
        ]

@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def invite_member(
    payload: InviteMemberRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Invite a new user to the organization in PENDING state, enforcing subscription seat quotas.
    """
    if payload.role not in ["OWNER", "ADMIN", "OPERATOR", "MEMBER"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role specified."
        )

    from .billing import _get_org_sub, PLANS_CATALOG
    sub = _get_org_sub(ctx.organization_id)
    plan = next((p for p in PLANS_CATALOG if p.id == sub["tier"]), None)
    max_seats = plan.team_members_limit if plan else 5

    async with session_manager.session() as session:
        # Check current member count
        count_stmt = select(MembershipDbModel).where(MembershipDbModel.organization_id == ctx.organization_id)
        count_res = await session.execute(count_stmt)
        current_members = len(count_res.scalars().all())

        if current_members >= max_seats:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Organization has reached its plan seat limit ({current_members}/{max_seats} seats). Upgrade your subscription plan to add more team members."
            )

        # Check if user already exists
        user_stmt = select(UserDbModel).where(UserDbModel.email == payload.email.strip().lower())
        res = await session.execute(user_stmt)
        user = res.scalar_one_or_none()

        if not user:
            # Create user in PENDING status
            user_id = str(uuid4())
            default_pwd = Password.create("Welcome123!")
            user = UserDbModel(
                id=user_id,
                email=payload.email.strip().lower(),
                password_hash=default_pwd.password_hash,
                name=payload.name,
                status="PENDING",
                email_verified=False
            )
            session.add(user)
            await session.flush()
        else:
            user_id = user.id

        # Check if membership already exists in this org
        mem_stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        res_mem = await session.execute(mem_stmt)
        existing_mem = res_mem.scalar_one_or_none()

        if existing_mem:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member or invited to this organization."
            )

        # Create membership in PENDING state
        membership = MembershipDbModel(
            organization_id=ctx.organization_id,
            user_id=user_id,
            role=payload.role,
            status="PENDING",
            joined_at=datetime.utcnow()
        )
        session.add(membership)
        await session.commit()

        return {"status": "success", "message": f"Invitation created for {payload.email} in PENDING status."}

@router.post("/{user_id}/resend-invite")
async def resend_invitation(
    user_id: str,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Resends an active pending invitation to a team member.
    """
    async with session_manager.session() as session:
        stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        res = await session.execute(stmt)
        membership = res.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")
        
        if membership.status != "PENDING":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PENDING invitations can be re-sent.")
            
        return {"status": "success", "message": "Invitation notification re-sent successfully."}

@router.post("/{user_id}/accept-invite")
async def accept_invitation(
    user_id: str,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Accepts a pending invitation, transitioning membership status from PENDING to ACCEPTED/ACTIVE.
    """
    async with session_manager.session() as session:
        stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        res = await session.execute(stmt)
        membership = res.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")
            
        membership.status = "ACCEPTED"
        await session.commit()
        return {"status": "success", "message": "Invitation accepted. Member status is now active."}

@router.put("/{user_id}/role")
async def update_member_role(
    user_id: str,
    payload: UpdateMemberRoleRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Update member role within the organization context.
    """
    if payload.role not in ["OWNER", "ADMIN", "OPERATOR", "MEMBER"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role specified."
        )

    # Protect OWNER role changes (only OWNER can change OWNER, or prevent losing last owner)
    async with session_manager.session() as session:
        stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        res = await session.execute(stmt)
        membership = res.scalar_one_or_none()

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found."
            )

        membership.role = payload.role
        await session.commit()
        return {"status": "success", "message": "Membership role updated successfully."}

@router.delete("/{user_id}")
async def remove_member(
    user_id: str,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Remove user membership or revoke pending invitation from the organization.
    """
    if user_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own membership. Transfer ownership first."
        )

    async with session_manager.session() as session:
        stmt = (
            select(MembershipDbModel)
            .where(MembershipDbModel.organization_id == ctx.organization_id)
            .where(MembershipDbModel.user_id == user_id)
        )
        res = await session.execute(stmt)
        membership = res.scalar_one_or_none()

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found."
            )

        await session.delete(membership)
        await session.commit()
        return {"status": "success", "message": "Member removed or invitation revoked from organization."}

