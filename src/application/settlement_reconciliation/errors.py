from ...domain.shared.result.result import ResultError

class SettlementReconciliationErrors:
    @staticmethod
    def settlement_not_found(message: str = "Settlement not found.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_FOUND", message)

    @staticmethod
    def unauthorized_organization_access(message: str = "Access to settlement is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ORGANIZATION_ACCESS", message)

    @staticmethod
    def duplicate_settlement(message: str = "Duplicate settlement reference or idempotency key.") -> ResultError:
        return ResultError("DUPLICATE_SETTLEMENT", message)

    @staticmethod
    def invalid_settlement_data(message: str = "Invalid settlement input data.") -> ResultError:
        return ResultError("INVALID_SETTLEMENT_DATA", message)

    @staticmethod
    def invalid_settlement_state(message: str = "Invalid settlement state transition.") -> ResultError:
        return ResultError("INVALID_SETTLEMENT_STATE", message)

    @staticmethod
    def settlement_already_completed(message: str = "Settlement is already completed.") -> ResultError:
        return ResultError("SETTLEMENT_ALREADY_COMPLETED", message)

    @staticmethod
    def settlement_already_cancelled(message: str = "Settlement is already cancelled.") -> ResultError:
        return ResultError("SETTLEMENT_ALREADY_CANCELLED", message)

    @staticmethod
    def settlement_entry_invalid(message: str = "Settlement entry is invalid.") -> ResultError:
        return ResultError("SETTLEMENT_ENTRY_INVALID", message)

    @staticmethod
    def duplicate_settlement_entry(message: str = "Duplicate settlement entry source transaction.") -> ResultError:
        return ResultError("DUPLICATE_SETTLEMENT_ENTRY", message)

    @staticmethod
    def settlement_currency_mismatch(message: str = "Settlement currency mismatch.") -> ResultError:
        return ResultError("SETTLEMENT_CURRENCY_MISMATCH", message)

    @staticmethod
    def settlement_not_balanced(message: str = "Settlement is not balanced.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_BALANCED", message)

    @staticmethod
    def settlement_discrepancy_exists(message: str = "Settlement has unresolved discrepancies.") -> ResultError:
        return ResultError("SETTLEMENT_DISCREPANCY_EXISTS", message)

    @staticmethod
    def settlement_discrepancy_not_found(message: str = "Settlement discrepancy not found.") -> ResultError:
        return ResultError("SETTLEMENT_DISCREPANCY_NOT_FOUND", message)

    @staticmethod
    def invalid_reconciliation_state(message: str = "Invalid reconciliation state.") -> ResultError:
        return ResultError("INVALID_RECONCILIATION_STATE", message)
