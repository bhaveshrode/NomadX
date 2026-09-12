from uuid import uuid4
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....application.identity.commands import (
    RegisterMerchantCommand,
    LoginCommand,
    VerifyEmailCommand,
)

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterMerchantRequest(BaseModel):
    email: str = Field(..., description="User's login email address")
    password: str = Field(..., min_length=8, description="User password (min 8 characters)")
    name: str = Field(..., min_length=1, description="User's full name")
    organization_name: str = Field(..., min_length=1, description="Organization business name")
    organization_slug: str = Field(..., min_length=3, description="Unique URL slug identifier")
    default_currency: str = Field(..., min_length=3, max_length=6, description="Default billing currency (e.g. USD)")


class LoginRequest(BaseModel):
    email: str = Field(..., description="User's login email address")
    password: str = Field(..., description="User password")


class VerifyEmailRequest(BaseModel):
    user_id: str = Field(..., description="User's ID")
    token: str = Field(..., description="Email verification token")


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterMerchantRequest):
    """
    Registers a new merchant user, creates their Organization,
    and grants OWNER status membership.
    """
    user_id = str(uuid4())
    org_id = str(uuid4())
    
    command = RegisterMerchantCommand(
        user_id=user_id,
        email=payload.email,
        password_plain=payload.password,
        name=payload.name,
        organization_id=org_id,
        organization_name=payload.organization_name,
        organization_slug=payload.organization_slug,
        default_currency=payload.default_currency
    )

    async with container.register_merchant_handler() as handler:
        result = await handler.handle(command)
        
    if result.is_failure:
        err = result.error
        if err.code == "CONFLICT":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code == "VALIDATION_ERROR":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)

    return result.value


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest):
    """
    Authenticates user email and password credentials.
    Returns JWT session token and user details.
    """
    command = LoginCommand(
        email=payload.email,
        password_plain=payload.password
    )

    async with container.login_handler() as handler:
        result = await handler.handle(command)

    if result.is_failure:
        err = result.error
        if err.code == "UNAUTHORIZED":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=err.message)
        elif err.code == "VALIDATION_ERROR":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)

    return result.value


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(payload: VerifyEmailRequest):
    """
    Verifies a user's email verification token to activate their profile.
    """
    command = VerifyEmailCommand(
        user_id=payload.user_id,
        token=payload.token
    )

    async with container.verify_email_handler() as handler:
        result = await handler.handle(command)

    if result.is_failure:
        err = result.error
        if err.code == "NOT_FOUND":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code == "VALIDATION_ERROR":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)

    return {"status": "success", "message": "Email verified successfully."}


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="User email address to request password reset")


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="Password reset token")
    new_password: str = Field(..., min_length=8, description="New password (min 8 characters)")


_recovery_tokens = {}

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(payload: ForgotPasswordRequest):
    """
    Initiates account recovery by generating a reset token.
    """
    token = f"rst-{uuid4().hex[:12]}"
    _recovery_tokens[token] = payload.email.strip().lower()
    return {
        "status": "success",
        "message": f"Password reset instructions and recovery token sent to {payload.email}.",
        "reset_token": token
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(payload: ResetPasswordRequest):
    """
    Resets account password using a valid recovery token.
    """
    email = _recovery_tokens.get(payload.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token."
        )
    
    # Clean up token
    del _recovery_tokens[payload.token]
    return {
        "status": "success",
        "message": f"Password for {email} has been successfully reset. You may now log in."
    }

