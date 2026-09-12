from datetime import timedelta
from typing import Dict, Optional
from ...domain.receivable.dunning import DunningStage

class DunningStageRule:
    """
    Configuration parameters for a single dunning stage.
    """
    def __init__(self, delay_days: int, action_type: str, max_attempts: int = 3, requires_approval: bool = False):
        self.delay_days = delay_days
        self.action_type = action_type
        self.max_attempts = max_attempts
        self.requires_approval = requires_approval


DEFAULT_DUNNING_RULES = {
    DunningStage.FIRST_REMINDER: DunningStageRule(
        delay_days=1,
        action_type="SEND_REMINDER",
        max_attempts=3,
        requires_approval=False
    ),
    DunningStage.SECOND_REMINDER: DunningStageRule(
        delay_days=7,
        action_type="SEND_REMINDER",
        max_attempts=3,
        requires_approval=False
    ),
    DunningStage.FINAL_NOTICE: DunningStageRule(
        delay_days=14,
        action_type="SEND_FINAL_NOTICE",
        max_attempts=3,
        requires_approval=False
    ),
    DunningStage.COLLECTION_ESCALATION: DunningStageRule(
        delay_days=30,
        action_type="ESCALATE_COLLECTION",
        max_attempts=1,
        requires_approval=True
    ),
}


class DunningPolicy:
    """
    Configurable policy defining when collection actions occur and stage progression criteria.
    """
    def __init__(self, rules: Optional[Dict[DunningStage, DunningStageRule]] = None):
        self._rules = rules or DEFAULT_DUNNING_RULES

    def get_next_stage(self, current_stage: DunningStage) -> Optional[DunningStage]:
        """
        Determines the next sequential dunning stage.
        """
        if current_stage == DunningStage.NONE:
            return DunningStage.FIRST_REMINDER
        elif current_stage == DunningStage.FIRST_REMINDER:
            return DunningStage.SECOND_REMINDER
        elif current_stage == DunningStage.SECOND_REMINDER:
            return DunningStage.FINAL_NOTICE
        elif current_stage == DunningStage.FINAL_NOTICE:
            return DunningStage.COLLECTION_ESCALATION
        return None

    def get_rule(self, stage: DunningStage) -> Optional[DunningStageRule]:
        """
        Gets rule configuration for a specific dunning stage.
        """
        return self._rules.get(stage)

    def is_eligible_for_stage(self, stage: DunningStage, days_overdue: int) -> bool:
        """
        Checks if the days overdue meets or exceeds the stage delay threshold.
        """
        rule = self.get_rule(stage)
        if not rule:
            return False
        return days_overdue >= rule.delay_days

    def get_delay_for_stage(self, stage: DunningStage) -> timedelta:
        """
        Returns delay duration as a timedelta object.
        """
        rule = self.get_rule(stage)
        if not rule:
            return timedelta(days=0)
        return timedelta(days=rule.delay_days)
