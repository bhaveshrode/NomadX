from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class EmailMessage:
    recipient: str
    subject: str
    body_html: str
    body_text: str
    sent_at: str

class EmailTemplateRenderer:
    @staticmethod
    def render_critical_anomaly(anomaly_title: str, severity: str, details: str) -> str:
        return f"""
        <!DOCTYPE html>
        <html>
        <head><style>body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }}
        .card {{ background: #1e293b; border-left: 4px solid #ef4444; padding: 16px; border-radius: 8px; }}</style></head>
        <body>
          <div class="card">
            <h2 style="color:#ef4444;">🚨 CRITICAL ANOMALY DETECTED</h2>
            <p><strong>Title:</strong> {anomaly_title}</p>
            <p><strong>Severity:</strong> {severity}</p>
            <p><strong>Details:</strong> {details}</p>
            <p><em>Action required: Login to Operations Console to view incident details.</em></p>
          </div>
        </body>
        </html>
        """

    @staticmethod
    def render_approval_request(title: str, requester: str, amount: str) -> str:
        return f"""
        <!DOCTYPE html>
        <html>
        <head><style>body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }}
        .card {{ background: #1e293b; border-left: 4px solid #3b82f6; padding: 16px; border-radius: 8px; }}</style></head>
        <body>
          <div class="card">
            <h2 style="color:#3b82f6;">✋ APPROVAL REQUIRED</h2>
            <p><strong>Request:</strong> {title}</p>
            <p><strong>Requester:</strong> {requester}</p>
            <p><strong>Amount / Value:</strong> {amount}</p>
            <p><a href="/console#approvals" style="background:#3b82f6;color:white;padding:8px 16px;text-decoration:none;border-radius:4px;">Review in Console</a></p>
          </div>
        </body>
        </html>
        """

class EmailService:
    def __init__(self):
        self.sent_emails = []

    async def send_email(self, recipient: str, subject: str, html_content: str, text_content: str = "") -> EmailMessage:
        msg = EmailMessage(
            recipient=recipient,
            subject=subject,
            body_html=html_content,
            body_text=text_content or subject,
            sent_at=datetime.utcnow().isoformat()
        )
        self.sent_emails.append(msg)
        return msg

email_service = EmailService()
