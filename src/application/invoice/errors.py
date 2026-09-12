from ...domain.shared.result.result import ResultError


class InvoiceErrors:
    """
    Standardized Application ResultErrors for Invoice management use cases.
    """
    @staticmethod
    def not_found(message: str = "Invoice not found.") -> ResultError:
        return ResultError("INVOICE_NOT_FOUND", message)

    @staticmethod
    def unauthorized(message: str = "Access denied to requested organization context.") -> ResultError:
        return ResultError("UNAUTHORIZED_ACCESS", message)

    @staticmethod
    def not_editable(message: str = "Invoice is not in editable DRAFT state.") -> ResultError:
        return ResultError("INVOICE_NOT_EDITABLE", message)

    @staticmethod
    def invalid_state(message: str = "Invoice lifecycle transition is invalid.") -> ResultError:
        return ResultError("INVALID_INVOICE_STATE", message)

    @staticmethod
    def duplicate_number(message: str = "Invoice number already exists within organization.") -> ResultError:
        return ResultError("DUPLICATE_INVOICE_NUMBER", message)

    @staticmethod
    def invalid_data(message: str = "Provided invoice data is invalid.") -> ResultError:
        return ResultError("INVALID_INVOICE_DATA", message)
