from typing import List, Dict, Any, Optional

from ..organization.value_objects import OrganizationId
from .value_objects import NotificationChannel
from .preferences import NotificationPreference


DEFAULT_POLICY_MAPPING = {
    "InvoiceIssued": {
        "template": "invoice.issued",
        "priority": "NORMAL",
        "recipient_type": "CUSTOMER",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: False}
    },
    "PaymentConfirmed": {
        "template": "payment.confirmed",
        "priority": "NORMAL",
        "recipient_type": "CUSTOMER",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: False}
    },
    "PaymentReconciled": {
        "template": "payment.reconciled",
        "priority": "NORMAL",
        "recipient_type": "CUSTOMER",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: False}
    },
    "ReceivableBecameOverdue": {
        "template": "receivable.overdue",
        "priority": "HIGH",
        "recipient_type": "CUSTOMER",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: False}
    },
    "ReceivableCollectionAttentionRequired": {
        "template": "receivable.attention",
        "priority": "HIGH",
        "recipient_type": "MERCHANT",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: True}
    },
    "SettlementSubmitted": {
        "template": "settlement.submitted",
        "priority": "NORMAL",
        "recipient_type": "MERCHANT",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: True}
    },
    "SettlementCompleted": {
        "template": "settlement.completed",
        "priority": "NORMAL",
        "recipient_type": "MERCHANT",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: True}
    },
    "SettlementFailed": {
        "template": "settlement.failed",
        "priority": "HIGH",
        "recipient_type": "MERCHANT",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: True}
    },
    "DunningStageChanged": {
        "template": "receivable.dunning_stage_changed",
        "priority": "HIGH",
        "recipient_type": "CUSTOMER",
        "channels": {NotificationChannel.EMAIL: True, NotificationChannel.WEBHOOK: False}
    }
}


class NotificationPolicy:
    """
    Domain Policy deciding whether a domain event warrants sending a notification,
    and if so, what channels, template, priority, and recipient target are mapped.
    """
    def __init__(self, preference_repo: Any) -> None:
        self._preference_repo = preference_repo

    def get_event_mapping(self, event_type: str) -> Optional[Dict[str, Any]]:
        """Returns the system default mapping dictionary for a given event type."""
        return DEFAULT_POLICY_MAPPING.get(event_type)

    async def get_enabled_channels(
        self,
        organization_id: OrganizationId,
        event_type: str
    ) -> List[NotificationChannel]:
        """
        Determines which channels are enabled for a given event under an organization,
        cross-referencing custom database preferences with built-in defaults.
        """
        mapping = DEFAULT_POLICY_MAPPING.get(event_type)
        if not mapping:
            return []

        enabled = []
        for ch in [NotificationChannel.EMAIL, NotificationChannel.WEBHOOK]:
            # Look up custom merchant preferences override in database
            pref = await self._preference_repo.find_preference(organization_id, event_type, ch)
            if pref is not None:
                if pref.is_enabled:
                    enabled.append(ch)
            else:
                # Fallback to default policy
                if mapping["channels"].get(ch, False):
                    enabled.append(ch)
        return enabled
