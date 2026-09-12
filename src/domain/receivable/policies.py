class CollectionPolicy:
    """
    Policy governing the business rules for when outstanding overdue receivables
    require dedicated collection attention escalation.
    """
    def __init__(self, overdue_attention_days: int = 31):
        self.overdue_attention_days = overdue_attention_days

    def requires_attention(self, days_overdue: int) -> bool:
        """Determines if the overdue duration meets the attention threshold."""
        return days_overdue >= self.overdue_attention_days
