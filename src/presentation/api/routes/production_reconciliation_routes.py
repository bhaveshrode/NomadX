from fastapi import APIRouter, Depends
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....infrastructure.workers.production_reconciliation_worker import ProductionFinancialReconciliationWorker

router = APIRouter(prefix="/api/financial-reconciliation", tags=["financial-reconciliation"])

@router.get("/live-sweep")
async def trigger_live_reconciliation_sweep(
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN", "OPERATOR"]))
):
    """
    Triggers a live continuous financial reconciliation sweep across invoices, payments, settlements, and allocations.
    """
    result = await ProductionFinancialReconciliationWorker.run_reconciliation_sweep(ctx.organization_id)
    return result
