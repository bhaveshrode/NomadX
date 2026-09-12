from typing import Any, Dict, List, Optional, TypeVar, Generic
from ..value_objects.value_object import deep_freeze

T = TypeVar("T")

class ResultError:
    """
    Class representing a standardized Domain Error in NomadX.
    Replaces raw string errors to support code categories, localization, and rich metadata.
    """
    def __init__(self, code: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        if not code or code.strip() == "":
            raise ValueError("ResultError code cannot be null or empty.")
        if not message or message.strip() == "":
            raise ValueError("ResultError message cannot be null or empty.")
        
        self._code = code.strip().upper()
        self._message = message.strip()
        
        if metadata:
            self._metadata = deep_freeze(dict(metadata))
        else:
            self._metadata = None

    @property
    def code(self) -> str:
        return self._code

    @property
    def message(self) -> str:
        return self._message

    @property
    def metadata(self) -> Optional[Dict[str, Any]]:
        return self._metadata

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    @classmethod
    def validation(cls, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ResultError":
        return cls("VALIDATION_ERROR", message, metadata)

    @classmethod
    def not_found(cls, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ResultError":
        return cls("NOT_FOUND", message, metadata)

    @classmethod
    def unauthorized(cls, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ResultError":
        return cls("UNAUTHORIZED", message, metadata)

    @classmethod
    def conflict(cls, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ResultError":
        return cls("CONFLICT", message, metadata)

    @classmethod
    def unexpected(cls, message: str, metadata: Optional[Dict[str, Any]] = None) -> "ResultError":
        return cls("INTERNAL_ERROR", message, metadata)


class Result(Generic[T]):
    """
    Lightweight generic class representing the outcome of an operation.
    A Result is either a Success carrying a value of type T, or a Failure carrying a ResultError.
    """
    def __init__(self, is_success: bool, error: Optional[ResultError] = None, value: Optional[T] = None):
        self._is_success = is_success
        self._error = error
        self._value = value

    @property
    def is_success(self) -> bool:
        return self._is_success

    @property
    def is_failure(self) -> bool:
        return not self._is_success

    @property
    def value(self) -> T:
        """Retrieves the successful value. Raises ValueError on failure result."""
        if self.is_failure:
            raise ValueError("Cannot access the value of a failure result. Check is_success first.")
        return self._value  # type: ignore

    @property
    def error(self) -> ResultError:
        """Retrieves the failure error. Raises ValueError on success result."""
        if self.is_success:
            raise ValueError("Cannot access the error of a success result. Check is_failure first.")
        return self._error  # type: ignore

    @classmethod
    def ok(cls, value: Optional[Any] = None) -> "Result[Any]":
        """Static factory representing a successful operation."""
        return cls(is_success=True, value=value)

    @classmethod
    def fail(cls, error: ResultError) -> "Result[Any]":
        """Static factory representing a failed operation."""
        if error is None:
            raise ValueError("Failure result must be provided with an error.")
        return cls(is_success=False, error=error)

    @classmethod
    def combine(cls, results: List["Result[Any]"]) -> "Result[None]":
        """
        Helper utility to combine multiple Results.
        Returns the first failure found, or a success if all passed.
        """
        for r in results:
            if r.is_failure:
                return cls.fail(r.error)
        return cls.ok()
