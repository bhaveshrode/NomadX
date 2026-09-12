from ...domain.shared.result.result import ResultError


class ReceivableErrors:
    @staticmethod
    def receivable_not_found(message: str = "Receivable not found.") -> ResultError:
        return ResultError("RECEIVABLE_NOT_FOUND", message)

    @staticmethod
    def unauthorized_organization_access(message: str = "Access to receivable is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ORGANIZATION_ACCESS", message)

    @staticmethod
    def unauthorized_access(message: str = "Access to receivable is unauthorized.") -> ResultError:
        # Retain alias for backward compatibility
        return ResultError("UNAUTHORIZED_ORGANIZATION_ACCESS", message)

    @staticmethod
    def duplicate_receivable(message: str = "Receivable already exists.") -> ResultError:
        return ResultError("DUPLICATE_RECEIVABLE", message)

    @staticmethod
    def invalid_receivable_data(message: str = "Invalid receivable data.") -> ResultError:
        return ResultError("INVALID_RECEIVABLE_DATA", message)

    @staticmethod
    def invalid_receivable_state(message: str = "Invalid receivable state.") -> ResultError:
        return ResultError("INVALID_RECEIVABLE_STATE", message)

    @staticmethod
    def receivable_already_settled(message: str = "Receivable is already settled.") -> ResultError:
        return ResultError("RECEIVABLE_ALREADY_SETTLED", message)

    @staticmethod
    def receivable_already_written_off(message: str = "Receivable is already written off.") -> ResultError:
        return ResultError("RECEIVABLE_ALREADY_WRITTEN_OFF", message)

    @staticmethod
    def payment_allocation_invalid(message: str = "Payment allocation is invalid.") -> ResultError:
        return ResultError("PAYMENT_ALLOCATION_INVALID", message)

    @staticmethod
    def payment_not_found(message: str = "Payment not found.") -> ResultError:
        return ResultError("PAYMENT_NOT_FOUND", message)

    @staticmethod
    def allocation_exceeds_outstanding(message: str = "Allocation amount exceeds outstanding balance.") -> ResultError:
        return ResultError("ALLOCATION_EXCEEDS_OUTSTANDING", message)

    @staticmethod
    def invalid_dunning_transition(message: str = "Invalid dunning stage transition.") -> ResultError:
        return ResultError("INVALID_DUNNING_TRANSITION", message)

    @staticmethod
    def invalid_allocation(message: str = "Invalid allocation amount or currency.") -> ResultError:
        # Retain alias for backward compatibility
        return ResultError("PAYMENT_ALLOCATION_INVALID", message)
