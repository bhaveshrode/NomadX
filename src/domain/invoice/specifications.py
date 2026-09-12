from ..shared.specifications.specification import Specification


# Forward declare Invoice to avoid circular import issues
class Invoice:
    pass


class InvoiceCanBeIssued(Specification["Invoice"]):
    """
    Specification to determine if an invoice is in a valid state to be issued.
    Requires DRAFT state, at least one line item, and a billing snapshot.
    """
    def is_satisfied_by(self, candidate: "Invoice") -> bool:
        from .invoice import InvoiceStatus
        
        # Must be in DRAFT state
        if candidate.lifecycle_status != InvoiceStatus.DRAFT:
            return False
            
        # Must have at least one line item
        if not candidate.line_items:
            return False
            
        # Must have a billing snapshot (buyer info)
        if not candidate.billing_snapshot:
            return False
            
        return True


class InvoiceCanBeVoided(Specification["Invoice"]):
    """
    Specification to determine if an invoice can be voided.
    An invoice can be voided if it is in DRAFT or ISSUED state (not already VOIDED).
    """
    def is_satisfied_by(self, candidate: "Invoice") -> bool:
        from .invoice import InvoiceStatus
        
        return candidate.lifecycle_status != InvoiceStatus.VOIDED
