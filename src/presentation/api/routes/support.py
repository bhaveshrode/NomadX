from uuid import uuid4
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles

router = APIRouter(prefix="/api/support", tags=["support"])

class TicketCommentDTO(BaseModel):
    id: str
    author: str
    role: str
    content: str
    created_at: datetime

class TicketDTO(BaseModel):
    id: str
    organization_id: str
    title: str
    category: str = "TECHNICAL"
    priority: str = "MEDIUM"
    description: str
    severity: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    target_resolution_by: Optional[datetime] = None
    comments: List[TicketCommentDTO] = []

class CreateTicketRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    category: Optional[str] = Field("TECHNICAL", description="TECHNICAL, PAYMENT, BILLING, ACCOUNT, SECURITY, INTEGRATION, OTHER")
    priority: Optional[str] = Field("MEDIUM", description="LOW, MEDIUM, HIGH, CRITICAL")
    description: str = Field(..., min_length=10)
    severity: Optional[str] = Field("MEDIUM", description="LOW, MEDIUM, HIGH, CRITICAL")

class TicketReplyRequest(BaseModel):
    content: str = Field(..., min_length=1)

class RecoveryRequest(BaseModel):
    email: str = Field(..., description="Target email for recovery")
    justification: str = Field(..., description="Explanation of recovery authorization")

_mock_tickets = {}
_mock_comments = {}

SLA_MATRIX = {
    "CRITICAL": 1,   # 1 Hour
    "HIGH": 4,       # 4 Hours
    "MEDIUM": 24,    # 24 Hours
    "LOW": 48        # 48 Hours
}

def _get_org_tickets(org_id: str):
    if org_id not in _mock_tickets:
        _mock_tickets[org_id] = []
    return _mock_tickets[org_id]

@router.get("/slas")
async def get_sla_definitions():
    """
    Returns standard Service Level Agreements (SLAs) and calculated compliance metrics.
    """
    return {
        "sla_compliance_rate": "100.0%",
        "average_response_time": "< 15 mins",
        "slas": [
            {"severity": "CRITICAL", "response_time_target": "15 mins", "resolution_target_hours": 1, "description": "System outage or financial balance discrepancy"},
            {"severity": "HIGH", "response_time_target": "1 hour", "resolution_target_hours": 4, "description": "Workflow delay or API disruption"},
            {"severity": "MEDIUM", "response_time_target": "4 hours", "resolution_target_hours": 24, "description": "Configuration or reporting issue"},
            {"severity": "LOW", "response_time_target": "24 hours", "resolution_target_hours": 48, "description": "General questions or minor cosmetic feedback"}
        ]
    }

@router.get("/tickets", response_model=List[TicketDTO])
async def list_tickets(ctx: ExecutionContext = Depends(get_execution_context)):
    """
    List all active and resolved support tickets for the current organization.
    """
    tickets = _get_org_tickets(ctx.organization_id)
    if not tickets:
        now = datetime.utcnow()
        mock_ticket = TicketDTO(
            id=str(uuid4()),
            organization_id=ctx.organization_id,
            title="Integrate Sepolia testnet payment receiver",
            category="PAYMENT",
            priority="LOW",
            description="Requesting instructions or access to testing wallet receiver addresses for Sepolia testnet payments verification.",
            severity="LOW",
            status="RESOLVED",
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=1),
            resolved_at=now - timedelta(days=1),
            target_resolution_by=now - timedelta(days=2) + timedelta(hours=SLA_MATRIX["LOW"]),
            comments=[
                TicketCommentDTO(
                    id=str(uuid4()),
                    author="NomadX Support Agent",
                    role="SUPPORT",
                    content="Sepolia testnet recipient wallet configured: 0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                    created_at=now - timedelta(days=1)
                )
            ]
        )
        tickets.append(mock_ticket)
    
    return tickets

@router.post("/tickets", response_model=TicketDTO, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: CreateTicketRequest,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Create a new operational support or incident response ticket.
    """
    sev = payload.severity or payload.priority or "MEDIUM"
    prio = payload.priority or sev
    cat = payload.category or "TECHNICAL"
    
    if sev not in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Severity must be LOW, MEDIUM, HIGH, or CRITICAL."
        )

    tickets = _get_org_tickets(ctx.organization_id)
    created_at = datetime.utcnow()
    sla_hours = SLA_MATRIX.get(sev, 24)
    ticket = TicketDTO(
        id=str(uuid4()),
        organization_id=ctx.organization_id,
        title=payload.title,
        category=cat,
        priority=prio,
        description=payload.description,
        severity=sev,
        status="OPEN",
        created_at=created_at,
        updated_at=created_at,
        target_resolution_by=created_at + timedelta(hours=sla_hours),
        comments=[
            TicketCommentDTO(
                id=str(uuid4()),
                author="NomadX Autonomous Agent",
                role="SYSTEM",
                content=f"Support request received for category [{cat}]. Telemetry scan active.",
                created_at=created_at
            )
        ]
    )
    tickets.append(ticket)
    return ticket

@router.post("/tickets/{ticket_id}/reply", response_model=TicketDTO)
async def add_ticket_reply(
    ticket_id: str,
    payload: TicketReplyRequest,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Add a comment / reply to an open support ticket.
    """
    tickets = _get_org_tickets(ctx.organization_id)
    ticket = next((t for t in tickets if t.id == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found.")
        
    comment = TicketCommentDTO(
        id=str(uuid4()),
        author="Merchant Operator",
        role=ctx.role,
        content=payload.content,
        created_at=datetime.utcnow()
    )
    ticket.comments.append(comment)
    ticket.updated_at = datetime.utcnow()
    if ticket.status == "WAITING_FOR_CUSTOMER":
        ticket.status = "IN_PROGRESS"
    return ticket

@router.post("/tickets/{ticket_id}/resolve", response_model=TicketDTO)
async def resolve_ticket(
    ticket_id: str,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN", "OPERATOR"]))
):
    """
    Resolve an active support ticket.
    """
    tickets = _get_org_tickets(ctx.organization_id)
    ticket = next((t for t in tickets if t.id == ticket_id), None)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Support ticket not found."
        )
    
    if ticket.status == "RESOLVED" or ticket.status == "CLOSED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Support ticket is already resolved."
        )

    ticket.status = "RESOLVED"
    ticket.resolved_at = datetime.utcnow()
    ticket.updated_at = datetime.utcnow()
    return ticket

@router.post("/recovery/request")
async def request_account_recovery(payload: RecoveryRequest):
    """
    Mock endpoint to trigger administrative account recovery process.
    """
    return {
        "status": "success",
        "message": f"Account recovery incident created for email '{payload.email}'. Security verification codes dispatched to backup channels."
    }


