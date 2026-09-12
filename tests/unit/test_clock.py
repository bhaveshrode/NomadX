import pytest
from datetime import datetime, timedelta
from nomadx.src.domain.shared.clock.clock import Clock, ControllableClock, SystemClock

def test_system_clock_now():
    Clock.reset()
    now = Clock.now()
    # Check that it returns system UTC time approximately
    diff = abs((datetime.utcnow() - now).total_seconds())
    assert diff < 1.0

def test_test_clock_freeze_and_advance():
    frozen_time = datetime(2026, 8, 15, 10, 0, 0)
    test_clock = ControllableClock(frozen_time)
    Clock.set_provider(test_clock)

    assert Clock.now() == frozen_time

    # Advance hours
    test_clock.advance_hours(2)
    assert Clock.now() == datetime(2026, 8, 15, 12, 0, 0)

    # Set custom time
    new_time = datetime(2026, 12, 25, 8, 30, 0)
    test_clock.set_time(new_time)
    assert Clock.now() == new_time

    Clock.reset()
    # Ensure reset returns it to SystemClock
    assert isinstance(Clock._provider, SystemClock)
