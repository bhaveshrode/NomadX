# NomadX Security & Acceptable Use Policy (AUP)

**Effective Date:** September 2, 2026  
**Version:** 1.0.0

## 1. Acceptable Use Guidelines
Users and Organizations agree NOT to:
- Attempt unauthorized access to multi-tenant organization boundaries.
- Inject malicious payloads or execute denial-of-service attacks against API endpoints.
- Utilize NomadX workflows for illegal money laundering, sanctions evasion, or fraudulent invoicing.

## 2. Security Standards
- **Role-Based Access Control (RBAC):** Strict enforcement of `OWNER`, `ADMIN`, `OPERATOR`, and `MEMBER` privileges.
- **Payload Restrictions:** Maximum payload size capped at 1MB per request.
- **Idempotency Safeguards:** Unique idempotency key enforcement on state-changing financial transactions.
- **Rate Limiting:** Nginx API Gateway enforces IP and token rate limits to prevent brute-force abuse.
