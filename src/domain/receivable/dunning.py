from enum import Enum
from datetime import datetime
from typing import Optional
from ..shared.value_objects.value_object import ValueObject


class DunningStage(str, Enum):
    NONE = "NONE"
    FIRST_REMINDER = "FIRST_REMINDER"
    SECOND_REMINDER = "SECOND_REMINDER"
    FINAL_NOTICE = "FINAL_NOTICE"
    COLLECTION_ESCALATION = "COLLECTION_ESCALATION"


class DunningState(ValueObject):
    """
    Value Object representing collection/dunning tracking state.
    """
    def __init__(
        self,
        stage: DunningStage,
        attempts: int = 0,
        last_attempt_at: Optional[datetime] = None,
        next_attempt_at: Optional[datetime] = None
    ):
        if not isinstance(stage, DunningStage):
            try:
                stage = DunningStage(stage)
            except ValueError:
                raise ValueError(f"Invalid dunning stage: {stage}")

        if not isinstance(attempts, int) or attempts < 0:
            raise ValueError("Attempts count must be a non-negative integer.")

        if last_attempt_at is not None and not isinstance(last_attempt_at, datetime):
            raise ValueError("Last attempt timestamp must be a datetime object.")

        if next_attempt_at is not None and not isinstance(next_attempt_at, datetime):
            raise ValueError("Next attempt timestamp must be a datetime object.")

        super().__init__({
            "stage": stage,
            "attempts": attempts,
            "last_attempt_at": last_attempt_at,
            "next_attempt_at": next_attempt_at
        })

    @property
    def stage(self) -> DunningStage:
        return self.props["stage"]

    @property
    def attempts(self) -> int:
        return self.props["attempts"]

    @property
    def last_attempt_at(self) -> Optional[datetime]:
        return self.props["last_attempt_at"]

    @property
    def next_attempt_at(self) -> Optional[datetime]:
        return self.props["next_attempt_at"]

    @classmethod
    def initial(cls) -> "DunningState":
        """Factory method to construct initial DunningState."""
        return cls(stage=DunningStage.NONE, attempts=0)

    def advance(self, next_stage: DunningStage, attempt_time: datetime, next_attempt_time: Optional[datetime] = None) -> "DunningState":
        """Transitions to the next dunning stage and updates dunning attempts."""
        if next_stage == DunningStage.NONE:
            raise ValueError("Cannot advance back to NONE stage.")
        return DunningState(
            stage=next_stage,
            attempts=self.attempts + 1,
            last_attempt_at=attempt_time,
            next_attempt_at=next_attempt_time
        )
