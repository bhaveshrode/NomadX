from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from .value_objects import UserId, Email, Password
from .events import UserRegistered, EmailVerified, UserAuthenticated, UserLockedOut

class UserStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"

class User(AggregateRoot[UserId]):
    """
    Aggregate Root guarding User authentication credentials, state, and lockout metrics.
    """
    def __init__(self, id: UserId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def email(self) -> Email:
        return self._props["email"]

    @property
    def password(self) -> Password:
        return self._props["password"]

    @property
    def name(self) -> str:
        return self._props["name"]

    @property
    def status(self) -> UserStatus:
        return self._props["status"]

    @property
    def email_verified(self) -> bool:
        return self._props["email_verified"]

    @property
    def verification_token(self) -> Optional[str]:
        return self._props["verification_token"]

    @property
    def login_attempts(self) -> int:
        return self._props["login_attempts"]

    @property
    def locked_until(self) -> Optional[datetime]:
        return self._props["locked_until"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: UserId,
        email: Email,
        password: Password,
        name: str,
        verification_token: Optional[str] = None
    ) -> Result["User"]:
        """
        Factory method to create a new User in PENDING status.
        Registers the UserRegistered domain event.
        """
        if not id or not email or not password or not name:
            return Result.fail(ResultError.validation("Mandatory fields cannot be null."))

        now = Clock.now()
        props = {
            "email": email,
            "password": password,
            "name": name,
            "status": UserStatus.PENDING,
            "email_verified": False,
            "verification_token": verification_token,
            "login_attempts": 0,
            "locked_until": None,
            "created_at": now,
            "updated_at": now
        }

        user = cls(id, props)
        user._add_domain_event(UserRegistered(id, email, name))
        return Result.ok(user)

    def verify_email(self, token: str) -> Result[None]:
        """
        Verifies the user's email address using a verification token.
        Transitions user to ACTIVE status on success.
        """
        if self.email_verified:
            return Result.ok()
        
        if not self.verification_token or self.verification_token != token:
            return Result.fail(ResultError.validation("Invalid or expired email verification token."))

        self._props["email_verified"] = True
        self._props["status"] = UserStatus.ACTIVE
        self._props["verification_token"] = None
        self._props["updated_at"] = Clock.now()

        self._add_domain_event(EmailVerified(self.id))
        return Result.ok()

    def authenticate(self, plaintext_password: str) -> Result[None]:
        """
        Authenticates a plaintext password credential.
        Guards against lockout periods and increments attempts on failure.
        """
        now = Clock.now()

        # Check account lockout boundary
        if self.status == UserStatus.LOCKED or self.locked_until:
            if self.locked_until and now < self.locked_until:
                diff_mins = int((self.locked_until - now).total_seconds() / 60) + 1
                return Result.fail(
                    ResultError.unauthorized(
                        f"Account is temporarily locked. Try again in {diff_mins} minutes."
                    )
                )
            else:
                # Lockout period expired, reset to active/pending depending on verification
                self._props["status"] = (
                    UserStatus.ACTIVE if self.email_verified else UserStatus.PENDING
                )
                self._props["locked_until"] = None
                self._props["login_attempts"] = 0

        # Validate password credential
        if self.password.verify(plaintext_password):
            self._props["login_attempts"] = 0
            self._props["locked_until"] = None
            self._props["updated_at"] = now
            self._add_domain_event(UserAuthenticated(self.id))
            return Result.ok()
        else:
            # Increment failed attempts
            self._props["login_attempts"] += 1
            self._props["updated_at"] = now

            # Enforce lockout rules (max 5 consecutive failures)
            if self.login_attempts >= 5:
                lockout_duration = timedelta(minutes=15)
                locked_until = now + lockout_duration
                self._props["status"] = UserStatus.LOCKED
                self._props["locked_until"] = locked_until
                self._add_domain_event(UserLockedOut(self.id, locked_until))
                return Result.fail(
                    ResultError.unauthorized(
                        "Account locked due to consecutive login failures. Try again in 15 minutes."
                    )
                )
            
            remaining = 5 - self.login_attempts
            return Result.fail(
                ResultError.unauthorized(
                    f"Authentication failed. Invalid credentials. {remaining} attempts remaining."
                )
            )
