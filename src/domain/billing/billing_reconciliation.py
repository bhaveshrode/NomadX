from typing import Dict, Any
from dataclasses import dataclass

@dataclass
class BillingReconciliationReport:
    organization_id: str
    tier: str
    subscription_status: str
    billed_amount: float
    settled_volume: float
    discrepancy_detected: bool
    reconciliation_status: str

class BillingReconciliationService:
    @staticmethod
    def reconcile(org_id: str, sub: Dict[str, Any], settled_volume: float, invoice_count: int) -> BillingReconciliationReport:
        expected_price = sub.get("price_per_month", 0.0)
        status = sub.get("status", "TRIAL")
        
        discrepancy = False
        if status == "ACTIVE" and expected_price == 0.0 and sub.get("tier") != "TRIAL":
            discrepancy = True
            
        return BillingReconciliationReport(
            organization_id=org_id,
            tier=sub.get("tier", "TRIAL"),
            subscription_status=status,
            billed_amount=expected_price,
            settled_volume=settled_volume,
            discrepancy_detected=discrepancy,
            reconciliation_status="BALANCED" if not discrepancy else "ACTION_REQUIRED"
        )
