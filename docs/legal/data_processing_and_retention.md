# NomadX Data Processing Addendum (DPA) & Retention Policy

**Effective Date:** September 2, 2026  
**Version:** 1.0.0

## 1. Data Processing Roles
- **Customer:** Data Controller for all customer invoice data and end-payer contact information.
- **NomadX:** Data Processor processing data strictly according to Customer instructions and platform workflows.

## 2. Retention Schedules
| Data Category | Active Retention Period | Archive / Purge Schedule |
| :--- | :--- | :--- |
| **Financial Audit Logs** | 7 Years (Financial Compliance) | Immutable Cold Archive |
| **Operational Telemetry** | 90 Days | Automated Sanitization |
| **Notification Logs** | 180 Days | Soft Delete & Purge |
| **Deactivated Org Data**| 30 Days Post-Deactivation | Permanent Secure Eradication |

## 3. Data Eradication & Portability
Customers may request full export of organization financial ledgers via API or initiate organization deactivation which schedules data purging post the 30-day grace period.
