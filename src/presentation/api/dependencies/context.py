from typing import Optional, List
from fastapi import Depends, Header, HTTPException, status, Query
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
import jwt
from sqlalchemy import select

from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import MembershipDbModel, UserDbModel, OrganizationDbModel
from ....infrastructure.config.configuration import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

class ExecutionContext(BaseModel):
    user_id: str
    organization_id: str
    role: str


def require_roles(allowed_roles: List[str]):
    """
    Dependency guard enforcing role-based authorization for the execution context.
    """
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency


async def get_execution_context(
    header_token: Optional[str] = Depends(oauth2_scheme),
    query_token: Optional[str] = Query(None, alias="token"),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-ID")
) -> ExecutionContext:
    """
    Dependency resolving the caller's JWT and confirming active membership context.
    Enforces multi-tenant boundary checks.
    """
    token = header_token or query_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token claims."
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials session."
        )

    # Determine organization boundary context
    async with session_manager.session() as db_session:
        if x_organization_id:
            # Explicit org targeted: Verify membership exists
            stmt = (
                select(MembershipDbModel)
                .where(MembershipDbModel.organization_id == x_organization_id)
                .where(MembershipDbModel.user_id == user_id)
            )
            result = await db_session.execute(stmt)
            membership = result.scalar_one_or_none()
            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to requested organization context."
                )
            active_org_id = x_organization_id
            role = membership.role
        else:
            # No explicit org targeted: fallback to first membership available
            stmt = select(MembershipDbModel).where(MembershipDbModel.user_id == user_id)
            result = await db_session.execute(stmt)
            memberships = result.scalars().all()
            if not memberships:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User has no registered organization memberships."
                )
            # Take first membership found
            active_org_id = memberships[0].organization_id
            role = memberships[0].role

    return ExecutionContext(
        user_id=user_id,
        organization_id=active_org_id,
        role=role
    )
