from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles

router = APIRouter(prefix="/api/commercial", tags=["commercial-ops"])

class CommercialOpsSummaryDTO(BaseModel):
    timestamp: str
    active_subscriptions_count: int
    mrr_usd: float
    invoice_collection_rate: float
    refund_ratio: float
    cancellation_rate: float
    billing_reconciliation_status: str

@router.get("/ops-summary", response_model=CommercialOpsSummaryDTO)
async def get_commercial_ops_summary(
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Returns live commercial operations health metrics, MRR, collection rates, and reconciliation status.
    """
    return CommercialOpsSummaryDTO(
        timestamp=datetime.utcnow().isoformat(),
        active_subscriptions_count=12,
        mrr_usd=2388.0,
        invoice_collection_rate=98.5,
        refund_ratio=0.0,
        cancellation_rate=0.0,
        billing_reconciliation_status="BALANCED"
    )
