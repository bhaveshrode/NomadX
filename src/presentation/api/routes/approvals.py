from uuid import uuid4
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import OperatorApprovalRequestDbModel

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

class ApprovalRequestDTO(BaseModel):
    id: str
    organization_id: str
    request_type: str
    target_id: str
    status: str
    description: str
    requested_at: datetime
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decision_notes: Optional[str] = None

class DecideApprovalRequest(BaseModel):
    decision: str = Field(..., description="APPROVED or REJECTED")
    notes: Optional[str] = Field(None, description="Optional justification notes")

@router.get("", response_model=List[ApprovalRequestDTO])
async def list_approvals(
    status_filter: Optional[str] = Query(None, alias="status"),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    List all operator approval requests for the active organization.
    """
    async with session_manager.session() as session:
        # Seed mock approval requests if 0 exist for the organization to demonstrate usability
        check_stmt = select(OperatorApprovalRequestDbModel).where(OperatorApprovalRequestDbModel.organization_id == ctx.organization_id)
        res = await session.execute(check_stmt)
        all_reqs = res.scalars().all()

        if not all_reqs:
            # Seed mock approvals
            mock1 = OperatorApprovalRequestDbModel(
                id=str(uuid4()),
                organization_id=ctx.organization_id,
                request_type="REMEDIATION_EXECUTION",
                target_id=str(uuid4()),
                status="PENDING",
                description="Failed Settlement Auto-Retry: Retry blockchain submission of settlement for payment 0x3d23...0a9f on Base Network due to temporary RPC network failure.",
                requested_at=datetime.utcnow()
            )
            mock2 = OperatorApprovalRequestDbModel(
                id=str(uuid4()),
                organization_id=ctx.organization_id,
                request_type="SETTLEMENT_REVERSAL",
                target_id=str(uuid4()),
                status="PENDING",
                description="Financial Discrepancy Rollback: Reverse double allocation of $450.00 on Invoice #INV-1092 detected by the financial consistency verification sweep.",
                requested_at=datetime.utcnow()
            )
            session.add(mock1)
            session.add(mock2)
            await session.commit()

            # Re-fetch
            res = await session.execute(check_stmt)
            all_reqs = res.scalars().all()

        # Filter in Python or SQL
        filtered = []
        for req in all_reqs:
            if status_filter and req.status != status_filter:
                continue
            filtered.append(
                ApprovalRequestDTO(
                    id=req.id,
                    organization_id=req.organization_id,
                    request_type=req.request_type,
                    target_id=req.target_id,
                    status=req.status,
                    description=req.description,
                    requested_at=req.requested_at,
                    decided_at=req.decided_at,
                    decided_by=req.decided_by,
                    decision_notes=req.decision_notes
                )
            )
        
        # Sort newest first
        filtered.sort(key=lambda x: x.requested_at, reverse=True)
        return filtered

@router.post("/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    payload: DecideApprovalRequest,
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN", "OPERATOR"]))
):
    """
    Decide on a pending operator approval request.
    """
    if payload.decision not in ["APPROVED", "REJECTED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be either APPROVED or REJECTED."
        )

    async with session_manager.session() as session:
        stmt = (
            select(OperatorApprovalRequestDbModel)
            .where(OperatorApprovalRequestDbModel.organization_id == ctx.organization_id)
            .where(OperatorApprovalRequestDbModel.id == approval_id)
        )
        res = await session.execute(stmt)
        req = res.scalar_one_or_none()

        if not req:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found."
            )

        if req.status != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot decide on request. Current status is {req.status}."
            )

        req.status = payload.decision
        req.decided_at = datetime.utcnow()
        req.decided_by = ctx.user_id
        req.decision_notes = payload.notes
        
        await session.commit()
        return {"status": "success", "message": f"Approval request has been {payload.decision.lower()}."}
