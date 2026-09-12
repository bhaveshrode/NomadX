from ...domain.shared.result.result import ResultError


class SettlementErrors:
    @staticmethod
    def settlement_not_found(message: str = "Settlement not found.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_FOUND", message)

    @staticmethod
    def unauthorized_access(message: str = "Access to settlement is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ACCESS", message)

    @staticmethod
    def duplicate_settlement_reference(message: str = "Duplicate settlement reference.") -> ResultError:
        return ResultError("DUPLICATE_SETTLEMENT_REFERENCE", message)

    @staticmethod
    def duplicate_settlement_idempotency(message: str = "Duplicate idempotency key.") -> ResultError:
        return ResultError("DUPLICATE_SETTLEMENT_IDEMPOTENCY", message)

    @staticmethod
    def payment_not_found(message: str = "Payment not found.") -> ResultError:
        return ResultError("PAYMENT_NOT_FOUND", message)

    @staticmethod
    def payment_ownership_mismatch(message: str = "Payment belongs to another organization.") -> ResultError:
        return ResultError("PAYMENT_OWNERSHIP_MISMATCH", message)

    @staticmethod
    def payment_not_eligible(message: str = "Payment is not eligible for settlement.") -> ResultError:
        return ResultError("PAYMENT_NOT_ELIGIBLE", message)

    @staticmethod
    def insufficient_settlement_balance(message: str = "Insufficient available settlement balance.") -> ResultError:
        return ResultError("INSUFFICIENT_SETTLEMENT_BALANCE", message)

    @staticmethod
    def invalid_settlement_state(message: str = "Invalid settlement state transition.") -> ResultError:
        return ResultError("INVALID_SETTLEMENT_STATE", message)

    @staticmethod
    def settlement_not_editable(message: str = "Settlement is not editable in current state.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_EDITABLE", message)

    @staticmethod
    def settlement_not_cancellable(message: str = "Settlement cannot be cancelled in current state.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_CANCELLABLE", message)

    @staticmethod
    def settlement_not_retryable(message: str = "Settlement is not retryable in current state.") -> ResultError:
        return ResultError("SETTLEMENT_NOT_RETRYABLE", message)

    @staticmethod
    def invalid_settlement_destination(message: str = "Invalid settlement destination.") -> ResultError:
        return ResultError("INVALID_SETTLEMENT_DESTINATION", message)
