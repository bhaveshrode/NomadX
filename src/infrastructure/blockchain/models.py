from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ObservedTokenTransfer:
    """
    Network-neutral representation of a decrypted token transfer log event.
    """
    transaction_reference: str
    network: str
    token_address: str
    sender: str
    receiver: str
    raw_amount: int
    block_number: int
    observed_at: datetime
