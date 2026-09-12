import logging
from typing import Optional
from ...application.workflow.event_processor import WorkflowEventProcessor

logger = logging.getLogger(__name__)


class WorkflowWorker:
    """
    Worker coordinating background execution of Workflows.
    Invokes WorkflowEventProcessor to process transactional outbox events.
    """
    def __init__(self, processor: Optional[WorkflowEventProcessor] = None):
        self._processor = processor or WorkflowEventProcessor()

    async def process_pending_events(self) -> int:
        """
        Invokes outbox consumption, condition evaluation, and action execution pipelines.
        """
        try:
            processed = await self._processor.process_events()
            return processed
        except Exception as err:
            logger.exception("Error encountered during Workflow background processing.")
            raise err
