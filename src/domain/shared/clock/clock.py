from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

class IClock(ABC):
    """Contract representing the Clock abstraction in NomadX."""
    @abstractmethod
    def now(self) -> datetime:
        """Returns the current date and time."""
        pass


class SystemClock(IClock):
    """
    System Clock implementation used in production.
    Obtains time directly from the host system.
    """
    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


class ControllableClock(IClock):
    """
    Controllable Clock implementation used for tests.
    Allows freezing, shifting, and manually setting the time.
    """
    def __init__(self, initial_date: datetime = None):
        self._now = initial_date or datetime.now(timezone.utc).replace(tzinfo=None)

    def now(self) -> datetime:
        return self._now

    def set_time(self, date: datetime) -> None:
        """Manually sets the test time to a specific datetime."""
        if date is None:
            raise ValueError("Date cannot be null or undefined.")
        self._now = date

    def advance(self, td: timedelta) -> None:
        """Advances the test time by a set timedelta."""
        self._now += td

    def advance_seconds(self, seconds: float) -> None:
        """Advances the test time by a set number of seconds."""
        self.advance(timedelta(seconds=seconds))

    def advance_minutes(self, minutes: float) -> None:
        """Advances the test time by a set number of minutes."""
        self.advance(timedelta(minutes=minutes))

    def advance_hours(self, hours: float) -> None:
        """Advances the test time by a set number of hours."""
        self.advance(timedelta(hours=hours))

    def advance_days(self, days: float) -> None:
        """Advances the test time by a set number of days."""
        self.advance(timedelta(days=days))


class Clock:
    """
    Static registry wrapper class for obtaining the current time.
    Defaults to the production SystemClock, but can be swapped out
    (e.g., in unit tests) to use a ControllableClock.
    """
    _provider: IClock = SystemClock()

    @classmethod
    def set_provider(cls, provider: IClock) -> None:
        """Swaps the active time provider."""
        if provider is None:
            raise ValueError("Clock provider cannot be null or undefined.")
        cls._provider = provider

    @classmethod
    def reset(cls) -> None:
        """Resets the active provider back to the default production SystemClock."""
        cls._provider = SystemClock()

    @classmethod
    def now(cls) -> datetime:
        """Gets the current date/time from the active provider."""
        return cls._provider.now()
