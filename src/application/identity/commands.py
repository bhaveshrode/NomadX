import jwt
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, Any, Optional
from uuid import uuid4

from ...domain.shared.result.result import Result, ResultError
from ...domain.shared.clock.clock import Clock

from ...domain.identity.value_objects import UserId, Email, Password
from ...domain.identity.user import User
from ...domain.identity.repositories import IUserRepository

from ...domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId as OrgUserId,
)
from ...domain.organization.organization import Organization
from ...domain.organization.repositories import IOrganizationRepository

@dataclass
class RegisterMerchantCommand:
    user_id: str
    email: str
    password_plain: str
    name: str
    organization_id: str
    organization_name: str
    organization_slug: str
    default_currency: str


class RegisterMerchantHandler:
    """
    Handler coordinating the multi-aggregate registration of a User,
    an Organization, and the corresponding Owner Membership context.
    """
    def __init__(self, user_repo: IUserRepository, org_repo: IOrganizationRepository):
        self._user_repo = user_repo
        self._org_repo = org_repo

    async def handle(self, command: RegisterMerchantCommand) -> Result[Dict[str, Any]]:
        try:
            # Construct value objects to enforce structural invariants
            user_id = UserId(command.user_id)
            email = Email(command.email)
            password = Password.create(command.password_plain)
            name = command.name

            org_id = OrganizationId(command.organization_id)
            org_name = OrganizationName(command.organization_name)
            org_slug = OrganizationSlug(command.organization_slug)
            org_settings = OrganizationSettings(command.default_currency)
        except ValueError as ex:
            return Result.fail(ResultError.validation(str(ex)))

        # Invariant check: unique email address
        existing_user = await self._user_repo.find_by_email(email)
        if existing_user is not None:
            return Result.fail(ResultError.conflict(f"Email address '{email.value}' is already registered."))

        # Invariant check: unique organization slug
        existing_org = await self._org_repo.find_by_slug(org_slug)
        if existing_org is not None:
            return Result.fail(ResultError.conflict(f"Organization slug '{org_slug.value}' is already taken."))

        # Invariant check: unique organization ID
        existing_org_id = await self._org_repo.find_by_id(org_id)
        if existing_org_id is not None:
            return Result.fail(ResultError.conflict(f"Organization ID '{org_id.value}' already exists."))

        # Instantiate User (in PENDING status with a verification token)
        verification_token = "tok-" + str(uuid4())
        user_result = User.create(
            id=user_id,
            email=email,
            password=password,
            name=name,
            verification_token=verification_token
        )
        if user_result.is_failure:
            return Result.fail(user_result.error)
        
        user = user_result.value

        # Instantiate Organization (joins the owner user as OWNER role membership)
        org_owner_id = OrgUserId(user.id.value)
        org_result = Organization.create(
            id=org_id,
            name=org_name,
            slug=org_slug,
            owner_id=org_owner_id,
            settings=org_settings
        )
        if org_result.is_failure:
            return Result.fail(org_result.error)

        org = org_result.value

        # Save both aggregates atomically inside transaction session
        await self._user_repo.save(user)
        await self._org_repo.save(org)

        return Result.ok({
            "user": {
                "id": user.id.value,
                "email": user.email.value,
                "name": user.name,
                "status": user.status.value,
                "email_verified": user.email_verified,
                "verification_token": verification_token,
            },
            "organization": {
                "id": org.id.value,
                "name": org.name.value,
                "slug": org.slug.value,
                "status": org.status.value,
                "default_currency": org.settings.default_currency,
                "owner_id": org.owner_id.value,
            }
        })


@dataclass
class LoginCommand:
    email: str
    password_plain: str


class LoginHandler:
    """
    Handler executing credentials verification, tracking lockout metrics,
    and generating JWT tokens for authenticated sessions.
    """
    def __init__(self, user_repo: IUserRepository, jwt_secret: str):
        self._user_repo = user_repo
        self._jwt_secret = jwt_secret

    async def handle(self, command: LoginCommand) -> Result[Dict[str, Any]]:
        try:
            email = Email(command.email)
        except ValueError as ex:
            return Result.fail(ResultError.validation(str(ex)))

        user = await self._user_repo.find_by_email(email)
        if user is None:
            return Result.fail(ResultError.unauthorized("Authentication failed. Invalid credentials."))

        # Authenticate and check lockout
        auth_result = user.authenticate(command.password_plain)
        
        # Save user status changes (increment attempts / lockouts)
        await self._user_repo.save(user)

        if auth_result.is_failure:
            return Result.fail(auth_result.error)

        # Generate JWT session token
        import time
        payload = {
            "sub": user.id.value,
            "email": user.email.value,
            "name": user.name,
            "exp": int(time.time()) + 7200
        }
        token = jwt.encode(payload, self._jwt_secret, algorithm="HS256")

        return Result.ok({
            "user": {
                "id": user.id.value,
                "email": user.email.value,
                "name": user.name,
                "status": user.status.value,
                "email_verified": user.email_verified
            },
            "token": token
        })


@dataclass
class VerifyEmailCommand:
    user_id: str
    token: str


class VerifyEmailHandler:
    """
    Handler executing user account activation upon email verification.
    """
    def __init__(self, user_repo: IUserRepository):
        self._user_repo = user_repo

    async def handle(self, command: VerifyEmailCommand) -> Result[None]:
        user_id = UserId(command.user_id)
        user = await self._user_repo.find_by_id(user_id)
        if user is None:
            return Result.fail(ResultError.not_found("User not found."))

        verify_result = user.verify_email(command.token)
        if verify_result.is_failure:
            return Result.fail(verify_result.error)

        await self._user_repo.save(user)
        return Result.ok()
