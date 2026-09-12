from ...domain.shared.result.result import ResultError


class NotificationErrors:
    @staticmethod
    def notification_not_found(message: str = "Notification not found.") -> ResultError:
        return ResultError("NOTIFICATION_NOT_FOUND", message)

    @staticmethod
    def unauthorized_organization_access(message: str = "Access to notification is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ORGANIZATION_ACCESS", message)

    @staticmethod
    def duplicate_notification(message: str = "Duplicate notification.") -> ResultError:
        return ResultError("DUPLICATE_NOTIFICATION", message)

    @staticmethod
    def invalid_notification_state(message: str = "Invalid notification state transition.") -> ResultError:
        return ResultError("INVALID_NOTIFICATION_STATE", message)

    @staticmethod
    def invalid_notification_data(message: str = "Invalid notification parameters.") -> ResultError:
        return ResultError("INVALID_NOTIFICATION_DATA", message)

    @staticmethod
    def notification_not_retryable(message: str = "Notification is not retryable in current state.") -> ResultError:
        return ResultError("NOTIFICATION_NOT_RETRYABLE", message)

    @staticmethod
    def notification_already_processed(message: str = "Notification has already been processed.") -> ResultError:
        return ResultError("NOTIFICATION_ALREADY_PROCESSED", message)
