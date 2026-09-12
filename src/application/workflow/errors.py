from ...domain.shared.result.result import ResultError


class WorkflowErrors:
    @staticmethod
    def workflow_not_found(message: str = "Workflow not found.") -> ResultError:
        return ResultError("WORKFLOW_NOT_FOUND", message)

    @staticmethod
    def unauthorized_access(message: str = "Access to workflow is unauthorized.") -> ResultError:
        return ResultError("UNAUTHORIZED_ORGANIZATION_ACCESS", message)

    @staticmethod
    def duplicate_workflow(message: str = "Workflow already exists.") -> ResultError:
        return ResultError("DUPLICATE_WORKFLOW", message)

    @staticmethod
    def invalid_workflow_data(message: str = "Invalid workflow configuration data.") -> ResultError:
        return ResultError("INVALID_WORKFLOW_DATA", message)

    @staticmethod
    def invalid_workflow_state(message: str = "Invalid workflow status transition.") -> ResultError:
        return ResultError("INVALID_WORKFLOW_STATE", message)

    @staticmethod
    def invalid_workflow_version(message: str = "Workflow version definition is invalid.") -> ResultError:
        return ResultError("INVALID_WORKFLOW_VERSION", message)

    @staticmethod
    def workflow_version_not_found(message: str = "Workflow version not found.") -> ResultError:
        return ResultError("WORKFLOW_VERSION_NOT_FOUND", message)

    @staticmethod
    def workflow_already_active(message: str = "Workflow is already active.") -> ResultError:
        return ResultError("WORKFLOW_ALREADY_ACTIVE", message)

    @staticmethod
    def workflow_already_archived(message: str = "Workflow is archived and cannot be modified.") -> ResultError:
        return ResultError("WORKFLOW_ALREADY_ARCHIVED", message)
