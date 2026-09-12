from enum import Enum


class AgingBucket(str, Enum):
    """
    Enum representing overdue ranges. Derived dynamically from clock and due_date.
    """
    CURRENT = "CURRENT"
    DAYS_1_30 = "DAYS_1_30"
    DAYS_31_60 = "DAYS_31_60"
    DAYS_61_90 = "DAYS_61_90"
    DAYS_90_PLUS = "DAYS_90_PLUS"

    @classmethod
    def from_days_overdue(cls, days: int) -> "AgingBucket":
        """Maps a days overdue count to the matching aging bucket enum value."""
        if days <= 0:
            return cls.CURRENT
        elif 1 <= days <= 30:
            return cls.DAYS_1_30
        elif 31 <= days <= 60:
            return cls.DAYS_31_60
        elif 61 <= days <= 90:
            return cls.DAYS_61_90
        else:
            return cls.DAYS_90_PLUS
