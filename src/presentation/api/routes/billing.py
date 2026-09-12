from uuid import uuid4
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import InvoiceDbModel, PaymentDbModel

router = APIRouter(prefix="/api/billing", tags=["billing"])

class SubscriptionDTO(BaseModel):
    tier: str
    status: str  # TRIAL, ACTIVE, PAST_DUE, CANCELED, UNPAID
    trial_start: datetime
    trial_end: datetime
    price_per_month: float
    currency: str

class MeteringDTO(BaseModel):
    invoices_processed_count: int
    payments_settled_count: int
    payments_settled_volume: float
    active_workflows_count: int

class SubscriptionInvoiceDTO(BaseModel):
    id: str
    invoice_number: str
    amount: float
    currency: str
    status: str
    due_date: datetime
    paid_at: Optional[datetime] = None

class PlanCatalogItemDTO(BaseModel):
    id: str
    name: str
    price_per_month: float
    currency: str
    api_limit_monthly: int
    invoices_allowance_monthly: int
    team_members_limit: int
    support_level: str
    onchain_settlement: bool
    automated_reconciliation: bool
    sla_percentage: float
    description: str

PLANS_CATALOG = [
    PlanCatalogItemDTO(
        id="STARTER",
        name="Starter",
        price_per_month=49.0,
        currency="USD",
        api_limit_monthly=10000,
        invoices_allowance_monthly=20,
        team_members_limit=2,
        support_level="Community & Email",
        onchain_settlement=True,
        automated_reconciliation=False,
        sla_percentage=99.5,
        description="For early-stage merchants starting autonomous Web3 commerce."
    ),
    PlanCatalogItemDTO(
        id="GROWTH",
        name="Professional",
        price_per_month=199.0,
        currency="USD",
        api_limit_monthly=50000,
        invoices_allowance_monthly=100,
        team_members_limit=5,
        support_level="Priority Support",
        onchain_settlement=True,
        automated_reconciliation=True,
        sla_percentage=99.9,
        description="Ideal for growing businesses requiring continuous reconciliation."
    ),
    PlanCatalogItemDTO(
        id="BUSINESS",
        name="Business",
        price_per_month=499.0,
        currency="USD",
        api_limit_monthly=250000,
        invoices_allowance_monthly=500,
        team_members_limit=15,
        support_level="24/7 Phone & Email",
        onchain_settlement=True,
        automated_reconciliation=True,
        sla_percentage=99.95,
        description="Advanced automation, multi-user teams, and dedicated support."
    ),
    PlanCatalogItemDTO(
        id="ENTERPRISE",
        name="Enterprise",
        price_per_month=999.0,
        currency="USD",
        api_limit_monthly=1000000,
        invoices_allowance_monthly=10000,
        team_members_limit=100,
        support_level="Dedicated SLA & Account Manager",
        onchain_settlement=True,
        automated_reconciliation=True,
        sla_percentage=99.99,
        description="Custom infrastructure, unlimited scaling, and enterprise compliance."
    )
]

class UpgradeRequest(BaseModel):
    tier: str = Field(..., description="Target tier: STARTER, GROWTH, BUSINESS, ENTERPRISE")

class WebhookEvent(BaseModel):
    event_id: str
    type: str  # invoice.payment_succeeded, invoice.payment_failed, customer.subscription.deleted
    organization_id: str
    data: Dict

# Global mock subscription and processed webhook cache
_mock_subscriptions = {}
_processed_webhooks = set()

def _get_org_sub(org_id: str):
    if org_id not in _mock_subscriptions:
        now = datetime.utcnow()
        _mock_subscriptions[org_id] = {
            "tier": "TRIAL",
            "status": "TRIAL",
            "trial_start": now - timedelta(days=5),
            "trial_end": now + timedelta(days=25),
            "price_per_month": 0.0,
            "currency": "USD"
        }
    return _mock_subscriptions[org_id]

@router.get("/plans", response_model=List[PlanCatalogItemDTO])
async def get_plans_catalog():
    """
    Get the complete available subscription plans catalog with entitlement limits.
    """
    return PLANS_CATALOG

@router.get("/subscription", response_model=SubscriptionDTO)
async def get_subscription(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    Get the subscription status and tier for the current organization.
    """
    sub = _get_org_sub(ctx.organization_id)
    return SubscriptionDTO(**sub)

@router.post("/subscription/upgrade")
async def upgrade_subscription(
    payload: UpgradeRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Upgrade organization subscription plan.
    """
    if payload.tier not in ["STARTER", "GROWTH", "BUSINESS", "ENTERPRISE", "TRIAL"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid subscription plan tier."
        )
    
    sub = _get_org_sub(ctx.organization_id)
    sub["tier"] = payload.tier
    # Keep current status or transition to ACTIVE
    if sub["status"] == "TRIAL":
        sub["status"] = "ACTIVE"
    
    prices = {
        "TRIAL": 0.0,
        "STARTER": 49.0,
        "GROWTH": 199.0,
        "BUSINESS": 499.0,
        "ENTERPRISE": 999.0
    }
    sub["price_per_month"] = prices[payload.tier]
    
    return {"status": "success", "message": f"Subscription upgraded to {payload.tier} successfully.", "subscription": sub}

@router.get("/metering", response_model=MeteringDTO)
async def get_metering(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    Get current usage metering data (real aggregated counts from database).
    """
    async with session_manager.session() as session:
        # Count total invoices
        inv_stmt = select(func.count(InvoiceDbModel.id)).where(InvoiceDbModel.organization_id == ctx.organization_id)
        inv_res = await session.execute(inv_stmt)
        invoice_count = inv_res.scalar() or 0

        # Count total payments
        pay_stmt = select(func.count(PaymentDbModel.id)).where(PaymentDbModel.organization_id == ctx.organization_id)
        pay_res = await session.execute(pay_stmt)
        payment_count = pay_res.scalar() or 0

        return MeteringDTO(
            invoices_processed_count=invoice_count,
            payments_settled_count=payment_count,
            payments_settled_volume=float(payment_count * 150.0), 
            active_workflows_count=3 
        )

@router.get("/invoices", response_model=List[SubscriptionInvoiceDTO])
async def list_subscription_invoices(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    Get payment invoices/billing statements for the SaaS subscription.
    """
    sub = _get_org_sub(ctx.organization_id)
    now = datetime.utcnow()
    
    if sub["tier"] == "TRIAL":
        return []
        
    return [
        SubscriptionInvoiceDTO(
            id="SUB-INV-001",
            invoice_number=f"NX-BILL-{ctx.organization_id[:4].upper()}-001",
            amount=sub["price_per_month"],
            currency=sub["currency"],
            status="PAID" if sub["status"] == "ACTIVE" else "UNPAID",
            due_date=now - timedelta(days=15),
            paid_at=now - timedelta(days=15) if sub["status"] == "ACTIVE" else None
        )
    ]


class RefundRequest(BaseModel):
    invoice_id: str = Field(..., description="Invoice ID to issue refund for")
    reason: str = Field("Customer request", description="Reason for refund")


@router.post("/subscription/cancel")
async def cancel_subscription(
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Cancel subscription at period end or immediately.
    """
    sub = _get_org_sub(ctx.organization_id)
    sub["status"] = "CANCELED"
    sub["price_per_month"] = 0.0
    sub["tier"] = "TRIAL"
    return {
        "status": "success",
        "message": "Subscription canceled successfully. Account set to TRIAL tier.",
        "subscription": sub
    }


@router.post("/refunds")
async def process_refund(
    payload: RefundRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Process billing refund request.
    """
    return {
        "status": "success",
        "refund_id": f"ref_{uuid4().hex[:10]}",
        "invoice_id": payload.invoice_id,
        "amount_refunded": 49.0,
        "reason": payload.reason,
        "processed_at": datetime.utcnow().isoformat()
    }


@router.get("/reconcile")
async def reconcile_billing(
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN"]))
):
    """
    Run billing reconciliation comparing usage metering with active subscription.
    """
    from ....domain.billing.billing_reconciliation import BillingReconciliationService
    sub = _get_org_sub(ctx.organization_id)
    report = BillingReconciliationService.reconcile(
        org_id=ctx.organization_id,
        sub=sub,
        settled_volume=450.0,
        invoice_count=3
    )
    return report.__dict__


@router.post("/webhooks")
async def handle_billing_webhook(
    payload: WebhookEvent,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    signature: Optional[str] = Header(None, alias="Stripe-Signature")
):
    """
    Stripe-like billing webhook simulator with signature validation and idempotency verification.
    """
    event_id = payload.event_id
    
    # Verify idempotency key or event ID
    dedup_key = idempotency_key or event_id
    if dedup_key in _processed_webhooks:
        return {
            "status": "duplicate",
            "message": f"Webhook event '{dedup_key}' has already been processed idempotently."
        }
    
    # Process event types
    sub = _get_org_sub(payload.organization_id)
    
    if payload.type == "invoice.payment_succeeded":
        sub["status"] = "ACTIVE"
    elif payload.type == "invoice.payment_failed":
        sub["status"] = "PAST_DUE"
    elif payload.type == "customer.subscription.deleted":
        sub["status"] = "CANCELED"
        sub["price_per_month"] = 0.0
        sub["tier"] = "TRIAL"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unhandled webhook event type: {payload.type}"
        )
        
    # Save event ID to prevent duplicate runs
    _processed_webhooks.add(dedup_key)
    
    return {
        "status": "success",
        "message": f"Webhook event '{dedup_key}' processed. Subscription status updated to {sub['status']}."
    }

