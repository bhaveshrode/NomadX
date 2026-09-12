from datetime import datetime
from typing import Dict, List, Optional, Any

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from .value_objects import (
    WorkflowId,
    WorkflowReference,
    WorkflowName,
    WorkflowStatus,
    WorkflowVersion,
    WorkflowIdempotencyKey
)
from .triggers import WorkflowTrigger
from .conditions import ConditionGroup
from .actions import WorkflowAction
from .definitions import WorkflowDefinition
from .events import (
    WorkflowCreated,
    WorkflowActivated,
    WorkflowPaused,
    WorkflowArchived,
    WorkflowVersionCreated,
    WorkflowVersionActivated
)


class Workflow(AggregateRoot[WorkflowId]):
    """
    Workflow aggregate root representing a merchant-defined automation flow.
    Orchestrates business triggers, conditions, versioned definitions, and active states.
    """
    def __init__(self, id: WorkflowId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def reference(self) -> WorkflowReference:
        return self._props["reference"]

    @property
    def name(self) -> WorkflowName:
        return self._props["name"]

    @property
    def description(self) -> Optional[str]:
        return self._props.get("description")

    @property
    def status(self) -> WorkflowStatus:
        return self._props["status"]

    @property
    def definitions(self) -> Dict[int, WorkflowDefinition]:
        return self._props["definitions"]

    @property
    def active_version_number(self) -> Optional[int]:
        return self._props.get("active_version_number")

    @property
    def idempotency_key(self) -> WorkflowIdempotencyKey:
        return self._props["idempotency_key"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: WorkflowId,
        organization_id: OrganizationId,
        reference: WorkflowReference,
        name: WorkflowName,
        description: Optional[str],
        idempotency_key: WorkflowIdempotencyKey,
        trigger: Optional[WorkflowTrigger] = None,
        conditions: Optional[ConditionGroup] = None,
        actions: Optional[List[WorkflowAction]] = None
    ) -> Result["Workflow"]:
        """Factory method to construct a new Workflow in DRAFT status."""
        if None in (id, organization_id, reference, name, idempotency_key):
            return Result.fail(ResultError.validation("Mandatory workflow properties cannot be null."))

        now = Clock.now()
        props = {
            "organization_id": organization_id,
            "reference": reference,
            "name": name,
            "description": description,
            "status": WorkflowStatus.DRAFT,
            "definitions": {},
            "active_version_number": None,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now
        }

        instance = cls(id, props)
        instance._add_domain_event(WorkflowCreated(id, organization_id, now))

        # If a definition was supplied during creation, save it as Version 1
        if trigger or conditions or actions:
            # All must be present if any is specified to form a definition
            if not trigger or not conditions or not actions:
                return Result.fail(ResultError.validation("Trigger, conditions, and actions must all be supplied to build definition version 1."))
            
            res_version = instance.add_version(trigger, conditions, actions)
            if res_version.is_failure:
                return Result.fail(res_version.error)

        return Result.ok(instance)

    def add_version(
        self,
        trigger: WorkflowTrigger,
        conditions: ConditionGroup,
        actions: List[WorkflowAction]
    ) -> Result[WorkflowVersion]:
        """Creates and appends a new immutable definition version to the workflow."""
        from .specifications import WorkflowCanBeUpdated
        spec = WorkflowCanBeUpdated()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation("Cannot add version definition to archived workflow."))

        next_ver_num = max(self.definitions.keys()) + 1 if self.definitions else 1
        version = WorkflowVersion(next_ver_num)
        now = Clock.now()

        definition = WorkflowDefinition(
            version=version,
            trigger=trigger,
            conditions=conditions,
            actions=actions,
            created_at=now
        )

        from .specifications import WorkflowDefinitionIsValid
        spec_valid = WorkflowDefinitionIsValid()
        if not spec_valid.is_satisfied_by(definition):
            return Result.fail(ResultError.validation("Workflow version definition is invalid."))

        self._props["definitions"][next_ver_num] = definition
        self._props["updated_at"] = now

        self._add_domain_event(WorkflowVersionCreated(self.id, version, now))
        return Result.ok(version)

    def activate_version(self, version_number: int) -> Result[None]:
        """Selects a specific definition version to be active."""
        from .specifications import WorkflowCanBeUpdated
        spec_update = WorkflowCanBeUpdated()
        if not spec_update.is_satisfied_by(self):
            return Result.fail(ResultError.validation("Cannot update version on archived workflow."))

        if version_number not in self.definitions:
            return Result.fail(ResultError.not_found(f"Workflow version {version_number} not found."))

        from .specifications import WorkflowVersionCanBeActivated
        spec_ver = WorkflowVersionCanBeActivated(version_number)
        if not spec_ver.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Workflow version {version_number} is invalid and cannot be activated."))

        now = Clock.now()
        self._props["active_version_number"] = version_number
        self._props["updated_at"] = now

        self._add_domain_event(WorkflowVersionActivated(self.id, WorkflowVersion(version_number), now))
        return Result.ok(None)

    def activate(self) -> Result[None]:
        """Transitions draft/paused workflow status to ACTIVE."""
        from .specifications import WorkflowCanBeActivated
        spec = WorkflowCanBeActivated()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot activate workflow in state {self.status}."))

        now = Clock.now()
        # Set active version if none was explicitly configured
        if self.active_version_number is None:
            latest_version = max(self.definitions.keys())
            self._props["active_version_number"] = latest_version
            self._add_domain_event(WorkflowVersionActivated(self.id, WorkflowVersion(latest_version), now))

        self._props["status"] = WorkflowStatus.ACTIVE
        self._props["updated_at"] = now

        self._add_domain_event(WorkflowActivated(self.id, now))
        return Result.ok(None)

    def pause(self) -> Result[None]:
        """Transitions active workflow status to PAUSED."""
        from .specifications import WorkflowCanBePaused
        spec = WorkflowCanBePaused()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot pause workflow in state {self.status}."))

        now = Clock.now()
        self._props["status"] = WorkflowStatus.PAUSED
        self._props["updated_at"] = now

        self._add_domain_event(WorkflowPaused(self.id, now))
        return Result.ok(None)

    def archive(self) -> Result[None]:
        """Transitions active/paused workflow status to ARCHIVED."""
        from .specifications import WorkflowCanBeArchived
        spec = WorkflowCanBeArchived()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot archive workflow in state {self.status}."))

        now = Clock.now()
        self._props["status"] = WorkflowStatus.ARCHIVED
        self._props["updated_at"] = now

        self._add_domain_event(WorkflowArchived(self.id, now))
        return Result.ok(None)

    def update_metadata(self, name: WorkflowName, description: Optional[str]) -> Result[None]:
        """Modifies name and description metadata of the workflow."""
        from .specifications import WorkflowCanBeUpdated
        spec = WorkflowCanBeUpdated()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation("Cannot update metadata on archived workflow."))

        if not name:
            return Result.fail(ResultError.validation("Workflow name cannot be empty."))

        now = Clock.now()
        self._props["name"] = name
        self._props["description"] = description
        self._props["updated_at"] = now
        return Result.ok(None)
