from fastapi import APIRouter
from pathlib import Path

router = APIRouter(prefix="/api/legal", tags=["legal"])

LEGAL_DIR = Path(__file__).parents[4] / "docs" / "legal"

def _read_doc(filename: str) -> str:
    path = LEGAL_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Document '{filename}' not found."

@router.get("/policies")
async def get_legal_policies():
    """
    Returns legal policy metadata and full document texts.
    """
    return {
        "version": "1.0.0",
        "last_updated": "2026-09-02",
        "policies": {
            "terms_of_service": {
                "title": "Terms of Service",
                "content": _read_doc("terms_of_service.md")
            },
            "privacy_policy": {
                "title": "Privacy Policy",
                "content": _read_doc("privacy_policy.md")
            },
            "data_processing": {
                "title": "Data Processing & Retention Policy",
                "content": _read_doc("data_processing_and_retention.md")
            },
            "acceptable_use": {
                "title": "Security & Acceptable Use Policy",
                "content": _read_doc("security_and_acceptable_use.md")
            },
            "financial_compliance": {
                "title": "Financial Regulatory Compliance Assessment",
                "content": _read_doc("financial_regulatory_compliance.md")
            }
        }
    }
