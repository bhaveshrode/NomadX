from uuid import uuid4
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles

router = APIRouter(prefix="/api/feedback", tags=["customer-feedback"])

class FeedbackSubmissionDTO(BaseModel):
    id: str
    organization_id: str
    category: str  # FEATURE_REQUEST, BUG_REPORT, USABILITY, INTEGRATION
    feedback_text: str
    priority_score: int
    status: str
    created_at: datetime

class SubmitFeedbackRequest(BaseModel):
    category: str = Field("FEATURE_REQUEST", description="FEATURE_REQUEST, BUG_REPORT, USABILITY, INTEGRATION")
    feedback_text: str = Field(..., min_length=5)

_feedback_store = []

@router.post("", response_model=FeedbackSubmissionDTO, status_code=status.HTTP_201_CREATED)
async def submit_customer_feedback(
    payload: SubmitFeedbackRequest,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Submits user feedback into the product iteration loop.
    """
    feedback = FeedbackSubmissionDTO(
        id=f"fbk-{uuid4().hex[:8]}",
        organization_id=ctx.organization_id,
        category=payload.category,
        feedback_text=payload.feedback_text,
        priority_score=3,
        status="UNDER_REVIEW",
        created_at=datetime.utcnow()
    )
    _feedback_store.append(feedback)
    return feedback

@router.get("/prioritization", response_model=List[FeedbackSubmissionDTO])
async def list_feedback_prioritization(
    ctx: ExecutionContext = Depends(require_roles(["OWNER", "ADMIN", "OPERATOR"]))
):
    """
    Lists prioritized customer feedback items for product backlog planning.
    """
    if not _feedback_store:
        _feedback_store.append(FeedbackSubmissionDTO(
            id=f"fbk-{uuid4().hex[:8]}",
            organization_id=ctx.organization_id,
            category="INTEGRATION",
            feedback_text="Add automated QuickBooks CSV export for monthly accounting reconciliation.",
            priority_score=5,
            status="PLANNED",
            created_at=datetime.utcnow()
        ))
    return _feedback_store
