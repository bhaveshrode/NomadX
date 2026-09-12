import pytest
from datetime import datetime, timedelta
from nomadx.src.domain.shared.clock.clock import Clock, ControllableClock
from nomadx.src.domain.identity.value_objects import UserId, Email, Password
from nomadx.src.domain.identity.user import User, UserStatus

def test_user_creation_in_pending_status():
    user_id = UserId()
    email = Email("alex@example.com")
    password = Password.create("secure-password-1")
    
    user_result = User.create(
        id=user_id,
        email=email,
        password=password,
        name="Alex Smith",
        verification_token="tok-12345"
    )
    
    assert user_result.is_success
    user = user_result.value
    assert user.id == user_id
    assert user.email == email
    assert user.status == UserStatus.PENDING
    assert user.email_verified is False
    assert user.verification_token == "tok-12345"
    assert user.login_attempts == 0
    assert user.locked_until is None
    assert len(user.domain_events) == 1
    assert user.domain_events[0].event_name == "UserRegistered"


def test_user_email_verification():
    user = User.create(
        id=UserId(),
        email=Email("alex@example.com"),
        password=Password.create("secure-password-1"),
        name="Alex Smith",
        verification_token="tok-12345"
    ).value

    # Invalid token check
    invalid_result = user.verify_email("wrong-token")
    assert invalid_result.is_failure
    assert "Invalid or expired" in invalid_result.error.message
    assert user.email_verified is False
    assert user.status == UserStatus.PENDING

    # Valid token check
    valid_result = user.verify_email("tok-12345")
    assert valid_result.is_success
    assert user.email_verified is True
    assert user.status == UserStatus.ACTIVE
    assert user.verification_token is None
    
    # Check domain event
    events = [e.event_name for e in user.domain_events]
    assert "EmailVerified" in events


def test_user_authentication_success_and_failure():
    password_plain = "valid-pwd-123"
    user = User.create(
        id=UserId(),
        email=Email("alex@example.com"),
        password=Password.create(password_plain),
        name="Alex Smith",
        verification_token="tok-123"
    ).value

    # Authenticate with incorrect password
    failed_auth = user.authenticate("wrong-pwd")
    assert failed_auth.is_failure
    assert user.login_attempts == 1
    assert user.status == UserStatus.PENDING

    # Authenticate with correct password
    success_auth = user.authenticate(password_plain)
    assert success_auth.is_success
    assert user.login_attempts == 0
    assert user.status == UserStatus.PENDING  # still pending because email not verified yet, which is fine


def test_user_lockout_after_multiple_failures():
    Clock.reset()
    frozen_time = datetime(2026, 8, 15, 12, 0, 0)
    clock = ControllableClock(frozen_time)
    Clock.set_provider(clock)

    password_plain = "my-secure-password"
    user = User.create(
        id=UserId(),
        email=Email("alex@example.com"),
        password=Password.create(password_plain),
        name="Alex Smith"
    ).value

    # Fail authentication 4 times
    for _ in range(4):
        res = user.authenticate("wrong-password")
        assert res.is_failure

    assert user.login_attempts == 4
    assert user.status == UserStatus.PENDING
    assert user.locked_until is None

    # 5th failure trigger lockout
    fifth_res = user.authenticate("wrong-password")
    assert fifth_res.is_failure
    assert "Account locked" in fifth_res.error.message
    assert user.login_attempts == 5
    assert user.status == UserStatus.LOCKED
    assert user.locked_until == frozen_time + timedelta(minutes=15)

    # Attempting to login while locked out should fail instantly
    instant_res = user.authenticate(password_plain)
    assert instant_res.is_failure
    assert "temporarily locked" in instant_res.error.message

    # Advance clock by 16 minutes (lockout expired)
    clock.advance_minutes(16)

    # Authentication should now succeed
    expired_lock_auth = user.authenticate(password_plain)
    assert expired_lock_auth.is_success
    assert user.login_attempts == 0
    assert user.status == UserStatus.PENDING
    assert user.locked_until is None

    Clock.reset()
