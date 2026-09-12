from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.models import ProviderStateDbModel


class ProviderCircuitBreaker:
    """
    Database-backed Provider Circuit Breaker.
    Protects downstream delivery channels from cascading failures.
    """
    def __init__(self, session: AsyncSession, failure_threshold: int = 5, cooldown_seconds: int = 60) -> None:
        self.session = session
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

    async def _get_or_create_state(self, provider_name: str) -> ProviderStateDbModel:
        stmt = select(ProviderStateDbModel).where(ProviderStateDbModel.provider_name == provider_name)
        res = await self.session.execute(stmt)
        state = res.scalar_one_or_none()
        if not state:
            state = ProviderStateDbModel(
                provider_name=provider_name,
                circuit_state="CLOSED",
                consecutive_failures=0,
                last_success_at=None,
                last_failure_at=None,
                failure_rate=0.0
            )
            self.session.add(state)
            await self.session.flush()
        return state

    async def check_availability(self, provider_name: str) -> bool:
        """
        Verifies if the provider is available.
        If OPEN, checks if cooldown elapsed to probe in HALF_OPEN state.
        """
        state = await self._get_or_create_state(provider_name)
        if state.circuit_state == "CLOSED":
            return True

        if state.circuit_state == "OPEN":
            if state.last_failure_at:
                elapsed = (Clock.now() - state.last_failure_at).total_seconds()
                if elapsed >= self.cooldown_seconds:
                    # Transition to HALF_OPEN probe
                    state.circuit_state = "HALF_OPEN"
                    await self.session.flush()
                    return True
            return False

        # HALF_OPEN allows a single probe attempt
        return True

    async def record_success(self, provider_name: str) -> None:
        """Resets failure counts and closes the circuit on successful delivery."""
        state = await self._get_or_create_state(provider_name)
        state.circuit_state = "CLOSED"
        state.consecutive_failures = 0
        state.last_success_at = Clock.now()
        await self.session.flush()

    async def record_failure(self, provider_name: str) -> None:
        """Increments failure counts and opens the circuit if threshold is reached."""
        state = await self._get_or_create_state(provider_name)
        state.consecutive_failures += 1
        state.last_failure_at = Clock.now()

        if state.consecutive_failures >= self.failure_threshold:
            state.circuit_state = "OPEN"
        
        await self.session.flush()
