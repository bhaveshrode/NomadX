import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field

from nomadx.src.bootstrap.container import container
from nomadx.src.application.organization.commands import CreateOrganizationCommand
from nomadx.src.presentation.api.routes.auth import router as auth_router
from nomadx.src.presentation.api.routes.invoices import router as invoices_router
from nomadx.src.presentation.api.routes.payment_intents import router as payment_intents_router
from nomadx.src.presentation.api.routes.payments import router as payments_router
from nomadx.src.presentation.api.routes.receivables import router as receivables_router
from nomadx.src.presentation.api.routes.receivable_reports import router as receivable_reports_router
from nomadx.src.presentation.api.routes.settlements import router as settlements_router
from nomadx.src.presentation.api.routes.notifications import router as notifications_router
from nomadx.src.presentation.api.routes.notification_reports import router as notification_reports_router
from nomadx.src.presentation.api.routes.workflows import router as workflows_router
from nomadx.src.presentation.api.routes.workflow_executions import router as workflow_executions_router
from nomadx.src.presentation.api.routes.settlement_reconciliations import router as settlement_reconciliations_router
from nomadx.src.presentation.api.routes.financial_consistency import router as financial_consistency_router
from nomadx.src.presentation.api.routes.financial_recovery import router as financial_recovery_router
from nomadx.src.presentation.api.routes.observability import router as observability_router
from nomadx.src.presentation.api.routes.members import router as members_router
from nomadx.src.presentation.api.routes.approvals import router as approvals_router
from nomadx.src.presentation.api.routes.billing import router as billing_router
from nomadx.src.presentation.api.routes.support import router as support_router
from nomadx.src.presentation.api.routes.organization_lifecycle import router as organization_lifecycle_router
from nomadx.src.presentation.api.routes.status import router as status_router
from nomadx.src.presentation.api.routes.legal_routes import router as legal_router
from nomadx.src.presentation.api.routes.customer_onboarding_tracker import router as customer_onboarding_tracker_router
from nomadx.src.presentation.api.routes.production_reconciliation_routes import router as production_reconciliation_routes_router
from nomadx.src.presentation.api.routes.metrics_slo import router as metrics_slo_router
from nomadx.src.presentation.api.routes.customer_feedback import router as customer_feedback_router
from nomadx.src.presentation.api.routes.commercial_ops import router as commercial_ops_router
from nomadx.src.presentation.api.routes.security_ops_routes import router as security_ops_routes_router
from nomadx.src.presentation.api.routes.product_analytics import router as product_analytics_router
from nomadx.src.presentation.api.routes.events import router as events_router
from nomadx.src.presentation.api.console_html import CONSOLE_HTML
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Define FastAPI Lifespan to manage resources
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB session manager & ensure tables exist
    container.init_resources()
    await session_manager.create_tables()
    yield
    # Shutdown: Close database pools
    await container.close_resources()

app = FastAPI(
    title="NomadX API",
    description="Autonomous Commerce System API Gateway",
    version="0.1.0",
    lifespan=lifespan
)

app.include_router(auth_router)
app.include_router(invoices_router)
app.include_router(payment_intents_router)
app.include_router(payments_router)
app.include_router(receivable_reports_router)
app.include_router(receivables_router)
app.include_router(settlements_router)
app.include_router(notifications_router)
app.include_router(notification_reports_router)
app.include_router(workflows_router)
app.include_router(workflow_executions_router)
app.include_router(settlement_reconciliations_router)
app.include_router(financial_consistency_router)
app.include_router(financial_recovery_router)
app.include_router(observability_router)
app.include_router(members_router)
app.include_router(approvals_router)
app.include_router(billing_router)
app.include_router(support_router)
app.include_router(organization_lifecycle_router)
app.include_router(status_router)
app.include_router(legal_router)
app.include_router(customer_onboarding_tracker_router)
app.include_router(production_reconciliation_routes_router)
app.include_router(metrics_slo_router)
app.include_router(customer_feedback_router)
app.include_router(commercial_ops_router)
app.include_router(security_ops_routes_router)
app.include_router(product_analytics_router)
app.include_router(events_router)

STATIC_DIR = Path(__file__).parent.parent.parent / "src" / "presentation" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def get_checkout_html() -> str:
    checkout_file = STATIC_DIR / "checkout.html"
    if checkout_file.exists():
        return checkout_file.read_text(encoding="utf-8")
    return "<html><body><h1>NomadX Web3 Checkout Portal</h1></body></html>"

CHECKOUT_HTML = get_checkout_html()






# HTTP Request Pydantic Schemas
class CreateOrganizationRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100, description="The name of the organization")
    slug: str = Field(..., min_length=3, max_length=50, description="A unique URL slug (lowercase, alphanumeric, hyphens)")
    owner_id: str = Field(..., description="Unique ID of the owner user")
    default_currency: str = Field(..., min_length=3, max_length=6, description="Default Currency ticker (e.g. USD, EUR)")

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from nomadx.src.presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles

@app.middleware("http")
async def limit_payload_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        if int(content_length) > 1048576:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={"detail": "Request payload too large. Max allowed size is 1MB."}
            )
    return await call_next(request)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()}
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please contact support."}
    )

from sqlalchemy import select
from fastapi.responses import JSONResponse
from datetime import datetime
from nomadx.src.infrastructure.database.session import session_manager

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Liveness and Readiness probe."""
    db_status = "healthy"
    try:
        async with session_manager.session() as session:
            await session.execute(select(1))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    status_code = status.HTTP_200_OK if "unhealthy" not in db_status else status.HTTP_503_SERVICE_UNAVAILABLE
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if status_code == 200 else "unhealthy",
            "service": "nomadx-api",
            "timestamp": datetime.utcnow().isoformat(),
            "dependencies": {
                "database": db_status
            }
        }
    )

@app.post("/api/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: CreateOrganizationRequest,
    ctx: ExecutionContext = Depends(require_roles(["SYSTEM", "PLATFORM_ADMIN"]))
):
    """
    Registers a new Organization merchant boundary context.
    """
    # Automatically generate a new unique ID for the organization
    org_id = str(uuid.uuid4())
    
    command = CreateOrganizationCommand(
        organization_id=org_id,
        name=payload.name,
        slug=payload.slug,
        owner_id=payload.owner_id,
        default_currency=payload.default_currency
    )

    # Dispatch use case through container instance
    async with container.create_organization_handler() as handler:
        result = await handler.handle(command)

        if result.is_failure:
            err = result.error
            if err.code == "VALIDATION_ERROR":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail=err.message
                )
            elif err.code == "CONFLICT":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, 
                    detail=err.message
                )
            elif err.code == "UNAUTHORIZED":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, 
                    detail=err.message
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                    detail=err.message
                )

        return result.value


from nomadx.src.presentation.api.dependencies.context import get_execution_context, ExecutionContext

@app.get("/api/secure-context")
async def secure_context(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    Test endpoint verifying JWT authorization, roles, and tenant context isolation.
    """
    return {
        "user_id": ctx.user_id,
        "organization_id": ctx.organization_id,
        "role": ctx.role
    }


from nomadx.src.presentation.api.console_html import get_console_html

@app.get("/console", response_class=HTMLResponse)
async def serve_console():
    """
    Serves the modern Operations Console UI SPA.
    """
    return HTMLResponse(content=get_console_html())


@app.get("/checkout/{intent_id}", response_class=HTMLResponse)
@app.get("/pay/{intent_id}", response_class=HTMLResponse)
async def serve_checkout(intent_id: str):
    """
    Serves the public customer Web3 Checkout Portal.
    """
    return HTMLResponse(content=get_checkout_html())



@app.get("/")
async def redirect_to_console():
    """
    Redirects root requests to the Operations Console.
    """
    return RedirectResponse(url="/console")


CHECKOUT_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NomadX Web3 Checkout Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #f3f4f6; }
        .glass { background: rgba(17, 24, 39, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-violet-950/30 via-slate-950 to-slate-950">

    <div class="w-full max-w-md glass rounded-2xl p-8 shadow-2xl relative overflow-hidden">
        <div class="flex items-center justify-between mb-6 border-b border-gray-800 pb-4">
            <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-lg bg-gradient-to-tr from-violet-600 to-indigo-500 flex items-center justify-center text-white font-bold shadow-lg">
                    <i class="fa-solid fa-plane-departure text-sm"></i>
                </div>
                <div>
                    <h1 class="text-lg font-bold font-outfit text-white tracking-tight">NomadX Pay</h1>
                    <p class="text-xs text-gray-400">Autonomous Web3 Checkout (Sandbox)</p>
                </div>
            </div>
            <span class="text-[10px] bg-amber-500/20 text-amber-300 font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider border border-amber-500/30"><i class="fa-solid fa-flask text-[9px] mr-1"></i> Base Sepolia · Sandbox</span>
        </div>

        <div id="checkoutLoading" class="text-center py-10 space-y-3">
            <div class="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-violet-500 mx-auto"></div>
            <p class="text-xs text-gray-400">Fetching payment intent details...</p>
        </div>

        <div id="checkoutContent" class="hidden space-y-5">
            <div class="bg-slate-900/80 p-5 rounded-xl border border-gray-800 space-y-3 font-mono text-xs">
                <div class="flex justify-between py-1 border-b border-gray-800/60">
                    <span class="text-gray-400 font-sans">Payment Reference:</span>
                    <span class="text-violet-300 font-bold" id="payRef">-</span>
                </div>
                <div class="flex justify-between py-1 border-b border-gray-800/60">
                    <span class="text-gray-400 font-sans">Amount Due:</span>
                    <span class="text-emerald-400 font-extrabold text-sm" id="payAmount">-</span>
                </div>
                <div class="flex justify-between py-1 border-b border-gray-800/60">
                    <span class="text-gray-400 font-sans">Asset & Rail:</span>
                    <span class="text-gray-300" id="payAsset">-</span>
                </div>
                <div class="flex justify-between py-1">
                    <span class="text-gray-400 font-sans">Merchant Address:</span>
                    <span class="text-gray-400 text-[11px] truncate max-w-[160px]" id="payWallet">-</span>
                </div>
            </div>

            <div id="payStatusBox" class="p-4 rounded-xl border text-center text-xs font-semibold hidden"></div>

            <button id="btnPayNow" onclick="executeCustomerPayment()" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-xl text-sm shadow-lg shadow-emerald-500/20 transition-all font-outfit flex items-center justify-center gap-2">
                <i class="fa-solid fa-bolt text-amber-300"></i> Confirm & Complete Payment
            </button>
        </div>
    </div>

    <script>
        const intentId = window.location.pathname.split("/").pop();
        let currentIntent = null;

        window.addEventListener("DOMContentLoaded", async () => {
            try {
                const res = await fetch(`/api/payment-intents/${intentId}`);
                if (!res.ok) {
                    const data = await res.json();
                    throw new Error(data.detail || "Payment intent not found or expired.");
                }
                currentIntent = await res.json();
                renderCheckout(currentIntent);
            } catch (err) {
                renderCheckout({
                    id: intentId,
                    payment_reference: "REF-" + intentId.slice(0, 8).toUpperCase(),
                    requested_amount: "100.00",
                    asset: { code: "USDC" },
                    network: "BASE",
                    destination: { identifier: "0x742d35Cc6634C0532925a3b844Bc454e4438f44e" },
                    status: "CREATED"
                });
            }
        });

        function renderCheckout(intent) {
            document.getElementById("checkoutLoading").classList.add("hidden");
            document.getElementById("checkoutContent").classList.remove("hidden");
            
            document.getElementById("payRef").innerText = intent.payment_reference || intent.id;
            document.getElementById("payAmount").innerText = `${intent.requested_amount} ${intent.asset ? intent.asset.code : 'USDC'}`;
            document.getElementById("payAsset").innerText = `${intent.asset ? intent.asset.code : 'USDC'} (${intent.network || 'BASE'})`;
            document.getElementById("payWallet").innerText = intent.destination ? intent.destination.identifier : "0x742d35...44e";

            if (intent.status === "PAID" || intent.status === "CONFIRMED") {
                showStatus("Payment is already COMPLETED and settled!", "success");
                document.getElementById("btnPayNow").classList.add("hidden");
            }
        }

        async function executeCustomerPayment() {
            const btn = document.getElementById("btnPayNow");
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> Processing Blockchain Transaction...`;

            try {
                const res = await fetch(`/api/payment-intents/${intentId}/complete-payment`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Payment settlement failed.");

                showStatus(`Payment Completed Successfully!<br><span class="font-mono text-[10px] text-gray-400 mt-1 block">Tx Hash: ${data.transaction_reference}</span>`, "success");
                btn.classList.add("hidden");
            } catch (err) {
                showStatus(`Payment failed: ${err.message}`, "error");
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-bolt text-amber-300"></i> Retry Payment`;
            }
        }

        function showStatus(msg, type) {
            const box = document.getElementById("payStatusBox");
            box.classList.remove("hidden", "bg-emerald-500/10", "border-emerald-500/40", "text-emerald-300", "bg-red-500/10", "border-red-500/40", "text-red-300");
            if (type === "success") {
                box.classList.add("bg-emerald-500/10", "border-emerald-500/40", "text-emerald-300");
            } else {
                box.classList.add("bg-red-500/10", "border-red-500/40", "text-red-300");
            }
            box.innerHTML = msg;
        }
    </script>
</body>
</html>
"""
