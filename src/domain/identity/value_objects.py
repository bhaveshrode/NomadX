import re
import bcrypt
from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject

class UserId(UniqueEntityID):
    """Identifier representing a User's identity."""
    pass


class Email(ValueObject):
    """Value object representing a validated email address."""
    def __init__(self, value: str):
        if not value or value.strip() == "":
            raise ValueError("Email address cannot be empty.")
        
        clean_email = value.strip().lower()
        # Basic email format check
        email_regex = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
        if not email_regex.match(clean_email):
            raise ValueError("Invalid email address format.")
            
        super().__init__({"value": clean_email})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class Password(ValueObject):
    """
    Value object wrapping hashed password credentials.
    Enforces minimum length constraints and wraps verification logic.
    """
    def __init__(self, password_hash: str):
        if not password_hash or password_hash.strip() == "":
            raise ValueError("Password hash cannot be empty.")
        super().__init__({"password_hash": password_hash})

    @property
    def password_hash(self) -> str:
        return self.props["password_hash"]

    @classmethod
    def create(cls, plaintext: str) -> "Password":
        """
        Hashes a plaintext password and constructs a new Password value object.
        Enforces password length policy of at least 8 characters.
        """
        if not plaintext or len(plaintext) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        
        # Hashing using bcrypt directly
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(plaintext.encode("utf-8"), salt)
        return cls(hashed.decode("utf-8"))

    @classmethod
    def from_hash(cls, password_hash: str) -> "Password":
        """Reconstructs a Password value object from an existing hash."""
        return cls(password_hash)

    def verify(self, plaintext: str) -> bool:
        """Verifies if the plaintext matches the stored hash."""
        if not plaintext:
            return False
        try:
            return bcrypt.checkpw(
                plaintext.encode("utf-8"), 
                self.password_hash.encode("utf-8")
            )
        except Exception:
            return False
