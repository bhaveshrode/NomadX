from decimal import Decimal
from datetime import datetime

class SourceTransaction:
    """
    Pure domain representation of an external source financial transaction (e.g. Payment)
    used during reconciliation matching.
    """
    def __init__(
        self,
        id: str,
        organization_id: str,
        transaction_reference: str,
        amount: Decimal,
        currency_code: str,
        observed_at: datetime,
        type: str = "PAYMENT",
        fees: Decimal = Decimal("0.00"),
        net_amount: Decimal = None
    ):
        self.id = id
        self.organization_id = organization_id
        self.transaction_reference = transaction_reference
        self.amount = amount
        self.currency_code = currency_code
        self.observed_at = observed_at
        self.type = type
        self.fees = fees
        self.net_amount = net_amount if net_amount is not None else (amount - fees)
