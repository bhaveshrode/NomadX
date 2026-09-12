import pytest
from nomadx.src.domain.identity.value_objects import Password

def test_password_policy_enforcement():
    # Valid passwords
    pwd = Password.create("super-secure-password-123")
    assert pwd is not None
    assert pwd.password_hash != "super-secure-password-123"

    # Too short passwords
    with pytest.raises(ValueError) as ex:
        Password.create("short")
    assert "at least 8 characters" in str(ex.value)

    # Empty passwords
    with pytest.raises(ValueError):
        Password.create("")


def test_password_verification():
    raw_pwd = "my-secure-password-999"
    pwd = Password.create(raw_pwd)

    # Valid matches
    assert pwd.verify(raw_pwd) is True

    # Invalid matches
    assert pwd.verify("wrong-password") is False
    assert pwd.verify("") is False


def test_password_from_hash():
    original_pwd = Password.create("some-password-key-abc")
    stored_hash = original_pwd.password_hash

    # Reconstruct from hash
    reconstructed = Password.from_hash(stored_hash)
    assert reconstructed.password_hash == stored_hash
    assert reconstructed.verify("some-password-key-abc") is True
