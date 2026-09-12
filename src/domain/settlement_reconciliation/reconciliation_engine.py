from datetime import datetime
from decimal import Decimal
from typing import List

from .settlement import Settlement, SettlementEntry
from .source_transaction import SourceTransaction
from .reconciliation import ReconciliationStatus, ReconciliationResult, ReconciliationDiscrepancy
from .value_objects import SettlementLineReference, SettlementAmount
from .matching_policy import (
    ExactReferenceMatchingPolicy,
    AmountCurrencyMatchingPolicy,
    MerchantMatchingPolicy,
    PeriodMatchingPolicy,
    ToleranceMatchingPolicy
)


class ReconciliationEngine:
    """
    Core domain service that executes matching policies to match settlement entries
    against source financial transactions.
    """
    def __init__(self):
        self.ref_policy = ExactReferenceMatchingPolicy()
        self.amount_policy = AmountCurrencyMatchingPolicy()
        self.merchant_policy = MerchantMatchingPolicy()
        self.period_policy = PeriodMatchingPolicy()
        self.tolerance_policy = ToleranceMatchingPolicy()

    def reconcile(self, settlement: Settlement, source_transactions: List[SourceTransaction]) -> ReconciliationResult:
        discrepancies: List[ReconciliationDiscrepancy] = []

        for entry in settlement.entries:
            # 1. Match source transactions using reference policy
            candidate_txs = [tx for tx in source_transactions if self.ref_policy.matches(entry, tx, settlement)]

            if not candidate_txs:
                # Discrepancy: MISSING_TRANSACTION
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        entry_reference=entry.id,
                        discrepancy_type="MISSING_TRANSACTION",
                        expected_amount=entry.net_amount,
                        actual_amount=SettlementAmount(Decimal("0.00")),
                        description=f"Source transaction for reference {entry.source_transaction_reference} not found in DB."
                    )
                )
                continue

            if len(candidate_txs) > 1:
                # Discrepancy: DUPLICATE_TRANSACTION
                actual_total = sum((tx.net_amount for tx in candidate_txs), Decimal("0.00"))
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        entry_reference=entry.id,
                        discrepancy_type="DUPLICATE_TRANSACTION",
                        expected_amount=entry.net_amount,
                        actual_amount=SettlementAmount(actual_total),
                        description=f"Multiple source transactions found matching reference {entry.source_transaction_reference}."
                    )
                )
                continue

            tx = candidate_txs[0]

            # 2. Check ownership/merchant
            if not self.merchant_policy.matches(entry, tx, settlement):
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        entry_reference=entry.id,
                        discrepancy_type="REFERENCE_MISMATCH",
                        expected_amount=entry.net_amount,
                        actual_amount=SettlementAmount(tx.net_amount),
                        description=f"Merchant ownership mismatch. Expected {settlement.organization_id.value}, got {tx.organization_id}."
                    )
                )
                continue

            # 3. Check period
            if not self.period_policy.matches(entry, tx, settlement):
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        entry_reference=entry.id,
                        discrepancy_type="PERIOD_MISMATCH",
                        expected_amount=entry.net_amount,
                        actual_amount=SettlementAmount(tx.net_amount),
                        description=f"Transaction date {tx.observed_at} falls outside the settlement period {settlement.period.start_date} to {settlement.period.end_date}."
                    )
                )
                continue

            # 4. Check currency
            if entry.currency.code.strip().upper() != tx.currency_code.strip().upper():
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        entry_reference=entry.id,
                        discrepancy_type="CURRENCY_MISMATCH",
                        expected_amount=entry.net_amount,
                        actual_amount=SettlementAmount(tx.net_amount),
                        description=f"Currency mismatch. Entry code: {entry.currency.code}, Tx code: {tx.currency_code}."
                    )
                )
                continue

            # 5. Check net amount
            if entry.net_amount.amount != tx.net_amount:
                # Check tolerance
                if self.tolerance_policy.matches(entry, tx, settlement):
                    # Automatically resolvable discrepancy
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            entry_reference=entry.id,
                            discrepancy_type="AMOUNT_MISMATCH",
                            expected_amount=entry.net_amount,
                            actual_amount=SettlementAmount(tx.net_amount),
                            description="Minor variance auto-resolved within tolerance limits.",
                            resolved=True
                        )
                    )
                else:
                    # Non-resolvable amount mismatch
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            entry_reference=entry.id,
                            discrepancy_type="AMOUNT_MISMATCH",
                            expected_amount=entry.net_amount,
                            actual_amount=SettlementAmount(tx.net_amount),
                            description=f"Monetary variance exceeding allowed tolerance. Expected {entry.net_amount.amount}, observed {tx.net_amount}."
                        )
                    )

        # Final outcome determination
        has_unresolved = any(not d.resolved for d in discrepancies)
        status = ReconciliationStatus.DISCREPANCY if (discrepancies and has_unresolved) else ReconciliationStatus.MATCHED

        desc = "Reconciliation complete. "
        if discrepancies:
            unres_count = sum(1 for d in discrepancies if not d.resolved)
            res_count = len(discrepancies) - unres_count
            desc += f"Detected {len(discrepancies)} discrepancies ({res_count} auto-resolved, {unres_count} unresolved)."
        else:
            desc += "All entries matched perfectly."

        return ReconciliationResult(
            status=status,
            discrepancies=discrepancies,
            verified_at=datetime.utcnow(),
            description=desc
        )
