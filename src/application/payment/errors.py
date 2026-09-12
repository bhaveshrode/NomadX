from ...domain.shared.result.result import ResultError


class PaymentErrors:
    @staticmethod
    def payment_not_found(message: str = "Payment not found.") -> ResultError:
        return ResultError("PAYMENT_NOT_FOUND", message)

    @staticmethod
    def payment_intent_not_found(message: str = "Payment intent not found.") -> ResultError:
        return ResultError("PAYMENT_INTENT_NOT_FOUND", message)

    @staticmethod
    def unauthorized_access(message: str = "Access to payment resource is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ACCESS", message)

    @staticmethod
    def duplicate_payment(message: str = "PAYMENT_ALREADY_EXISTS") -> ResultError:
        return ResultError("DUPLICATE_PAYMENT", message)

    @staticmethod
    def payment_intent_not_active(message: str = "Payment intent is not in ACTIVE state.") -> ResultError:
        return ResultError("PAYMENT_INTENT_NOT_ACTIVE", message)

    @staticmethod
    def payment_intent_expired(message: str = "Payment intent has expired.") -> ResultError:
        return ResultError("PAYMENT_INTENT_EXPIRED", message)

    @staticmethod
    def invalid_payment_state(message: str = "Invalid payment state transition.") -> ResultError:
        return ResultError("INVALID_PAYMENT_STATE", message)

    @staticmethod
    def invalid_payment_data(message: str = "Invalid payment data.") -> ResultError:
        return ResultError("INVALID_PAYMENT_DATA", message)

    @staticmethod
    def invoice_ownership_mismatch(message: str = "Invoice does not belong to the payment intent's organization.") -> ResultError:
        return ResultError("INVOICE_OWNERSHIP_MISMATCH", message)
