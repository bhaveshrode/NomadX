from contextlib import asynccontextmanager
from typing import AsyncGenerator

from ..infrastructure.config.configuration import settings, Settings
from ..infrastructure.database.session import session_manager
from ..infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresUserRepository,
    PostgresInvoiceRepository,
    PostgresPaymentIntentRepository,
    PostgresPaymentRepository,
    PostgresPaymentAllocationRepository,
    PostgresReceivableRepository,
    PostgresSettlementRepository,
    PostgresNotificationRepository,
    PostgresWorkflowRepository,
    PostgresCollectionActionRepository
)
from ..application.payment.commands import (
    CreatePaymentIntentHandler,
    ActivatePaymentIntentHandler,
    CancelPaymentIntentHandler,
    ExpirePaymentIntentHandler,
    DetectPaymentHandler,
    StartPaymentVerificationHandler,
    ConfirmPaymentHandler,
    RejectPaymentHandler,
    ReconcilePaymentHandler
)
from ..application.organization.commands import CreateOrganizationHandler
from ..application.identity.commands import (
    RegisterMerchantHandler,
    LoginHandler,
    VerifyEmailHandler,
)
from ..application.invoice.commands import (
    CreateInvoiceHandler,
    AddInvoiceLineItemHandler,
    UpdateInvoiceLineItemHandler,
    RemoveInvoiceLineItemHandler,
    AddInvoiceTaxLineHandler,
    RemoveInvoiceTaxLineHandler,
    AddInvoiceAdjustmentHandler,
    RemoveInvoiceAdjustmentHandler,
    IssueInvoiceHandler,
    VoidInvoiceHandler
)
from ..application.receivable import (
    CreateReceivableHandler,
    ApplyPaymentAllocationHandler,
    AllocatePaymentHandler,
    MarkReceivableOverdueHandler,
    SettleReceivableHandler,
    WriteOffReceivableHandler,
    AdvanceDunningStageHandler,
    UpdateReceivableAgingHandler,
    IReceivableQueryService
)
from ..application.settlement import (
    CreateSettlementHandler,
    StartSettlementProcessingHandler,
    SubmitSettlementHandler,
    ConfirmSettlementHandler,
    CompleteSettlementHandler,
    FailSettlementHandler,
    RetrySettlementHandler,
    CancelSettlementHandler,
    SettlementAvailabilityService
)
from ..application.settlement_reconciliation import (
    CreateSettlementHandler as CreateSettlementReconciliationHandler,
    OpenSettlementHandler as OpenSettlementReconciliationHandler,
    AddSettlementEntryHandler as AddSettlementEntryReconciliationHandler,
    AddSettlementAdjustmentHandler as AddSettlementAdjustmentReconciliationHandler,
    BeginReconciliationHandler,
    ReconcileSettlementHandler,
    RecordSettlementDiscrepancyHandler,
    ResolveSettlementDiscrepancyHandler,
    CompleteSettlementHandler as CompleteSettlementReconciliationHandler,
    CancelSettlementHandler as CancelSettlementReconciliationHandler
)
from ..infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository as PostgresSettlementReconciliationRepository
from ..application.notification.handlers import (
    CreateNotificationHandler,
    StartNotificationProcessingHandler,
    MarkNotificationDeliveredHandler,
    MarkNotificationFailedHandler,
    RetryNotificationHandler,
    CancelNotificationHandler
)
from ..application.notification.replay_handlers import ReplayNotificationHandler
from ..infrastructure.database.query_service import PostgresReceivableQueryService, PostgresNotificationQueryService, PostgresWorkflowQueryService
from ..infrastructure.database.settlement_reconciliation_query_service import PostgresSettlementQueryService
from ..application.financial_consistency.query_service import IFinancialConsistencyQueryService
from ..infrastructure.database.financial_consistency_query_service import PostgresFinancialConsistencyQueryService
from ..application.financial_recovery.service import IFinancialRecoveryService
from ..infrastructure.database.financial_recovery_service import PostgresFinancialRecoveryService
from ..application.observability.query_service import IObservabilityQueryService
from ..infrastructure.database.observability_query_service import PostgresObservabilityQueryService
from ..application.observability.telemetry_service import TelemetryService
from ..infrastructure.database.source_transaction_repository import PostgresSourceTransactionRepository
from ..infrastructure.workers.financial_consistency_automation_worker import FinancialConsistencyAutomationWorker
from ..application.settlement_reconciliation.automation_service import ReconciliationAutomationService
from ..infrastructure.workers.settlement_automation_worker import SettlementAutomationWorker
from ..infrastructure.workers.settlement_event_processor import SettlementEventProcessor
from ..application.notification.query_service import INotificationQueryService
from ..application.workflow.query_service import IWorkflowQueryService
from ..domain.workflow.repositories import IWorkflowRepository
from ..domain.receivable.repositories import IReceivableRepository
from ..application.workflow.handlers import (
    CreateWorkflowHandler,
    CreateWorkflowVersionHandler,
    ActivateWorkflowHandler,
    PauseWorkflowHandler,
    ArchiveWorkflowHandler,
    ActivateWorkflowVersionHandler
)
from ..application.notification.template_service import NotificationTemplateService
from ..infrastructure.database.repositories import PostgresNotificationTemplateRepository, PostgresNotificationPreferenceRepository
from ..application.notification.recipient_service import IRecipientResolver, RecipientResolver
from ..infrastructure.workers.notification_event_worker import NotificationEventWorker
from ..infrastructure.workers.workflow_worker import WorkflowWorker

from ..application.receivable.dunning_policy import DunningPolicy
from ..domain.receivable.repositories import ICollectionActionRepository
from ..infrastructure.workers.dunning_worker import DunningWorker
from ..infrastructure.workers.collection_action_worker import CollectionActionWorker
from ..infrastructure.workers.ar_event_processor import AREventProcessor
from ..application.receivable.collection_action_query_service import ICollectionActionQueryService
from ..infrastructure.database.collection_action_query_service import PostgresCollectionActionQueryService

from ..infrastructure.blockchain.registry import BlockchainAssetRegistry
from ..infrastructure.blockchain.clients.base_rpc import BaseRpcClient
from ..infrastructure.settlement.fake import FakeSettlementExecutionAdapter, FakeSettlementVerificationAdapter
from ..infrastructure.workers.settlement_worker import SettlementWorker
from ..infrastructure.workers.settlement_verification_worker import SettlementVerificationWorker
from ..infrastructure.notification.fake import (
    FakeEmailDeliveryAdapter,
    FakeWebhookDeliveryAdapter,
    NotificationDeliveryAdapter
)
from ..infrastructure.workers.notification_worker import NotificationWorker
from ..application.settlement.verification import SettlementVerificationService, ConfirmationPolicy as SettlementConfirmationPolicy
from ..application.payment.verification import (
    BlockchainClientProvider,
    ConfirmationPolicy,
    PaymentVerificationService
)


class Container:
    """
    Composition Root managing application configuration, databases,
    and dependency injection for application services.
    """
    def __init__(self):
        self.settings: Settings = settings
        self.blockchain_registry = BlockchainAssetRegistry()
        self.blockchain_policy = ConfirmationPolicy()
        self.blockchain_provider = BlockchainClientProvider()
        self.verification_service = PaymentVerificationService(
            self.blockchain_registry,
            self.blockchain_policy
        )
        
        # Register Base RPC client as default for BASE network
        if self.settings.base_rpc_url == "mock":
            from ..infrastructure.blockchain.clients.fake import FakeBlockchainClient
            self.blockchain_provider.register_client("BASE", FakeBlockchainClient(current_height=1005))
        else:
            self.blockchain_provider.register_client(
                "BASE",
                BaseRpcClient(self.settings.base_rpc_url)
            )
        self.settlement_execution_adapter = FakeSettlementExecutionAdapter()
        self.settlement_verification_adapter = FakeSettlementVerificationAdapter()
        self.settlement_confirmation_policy = SettlementConfirmationPolicy()
        self.settlement_verification_service = SettlementVerificationService(
            self.blockchain_registry,
            self.settlement_confirmation_policy
        )
        self.email_delivery_adapter = FakeEmailDeliveryAdapter()
        self.webhook_delivery_adapter = FakeWebhookDeliveryAdapter()
        self.notification_delivery_adapter = NotificationDeliveryAdapter(
            self.email_delivery_adapter,
            self.webhook_delivery_adapter
        )
        self._notification_worker = NotificationWorker(self.notification_delivery_adapter)

    def init_resources(self) -> None:
        """Initializes database engine and pool resources."""
        session_manager.init(self.settings.db_url)

    async def close_resources(self) -> None:
        """Disposes and cleans up database connections."""
        await session_manager.close()

    @asynccontextmanager
    async def create_organization_handler(self) -> AsyncGenerator[CreateOrganizationHandler, None]:
        """Dependency provider for CreateOrganizationHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresOrganizationRepository(session)
            handler = CreateOrganizationHandler(repo)
            yield handler

    @asynccontextmanager
    async def register_merchant_handler(self) -> AsyncGenerator[RegisterMerchantHandler, None]:
        """Dependency provider for RegisterMerchantHandler use case."""
        async with session_manager.session() as session:
            user_repo = PostgresUserRepository(session)
            org_repo = PostgresOrganizationRepository(session)
            handler = RegisterMerchantHandler(user_repo, org_repo)
            yield handler

    @asynccontextmanager
    async def login_handler(self) -> AsyncGenerator[LoginHandler, None]:
        """Dependency provider for LoginHandler use case."""
        async with session_manager.session() as session:
            user_repo = PostgresUserRepository(session)
            handler = LoginHandler(user_repo, self.settings.jwt_secret)
            yield handler

    @asynccontextmanager
    async def verify_email_handler(self) -> AsyncGenerator[VerifyEmailHandler, None]:
        """Dependency provider for VerifyEmailHandler use case."""
        async with session_manager.session() as session:
            user_repo = PostgresUserRepository(session)
            handler = VerifyEmailHandler(user_repo)
            yield handler

    @asynccontextmanager
    async def create_invoice_handler(self) -> AsyncGenerator[CreateInvoiceHandler, None]:
        """Dependency provider for CreateInvoiceHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield CreateInvoiceHandler(repo)

    @asynccontextmanager
    async def add_invoice_line_item_handler(self) -> AsyncGenerator[AddInvoiceLineItemHandler, None]:
        """Dependency provider for AddInvoiceLineItemHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield AddInvoiceLineItemHandler(repo)

    @asynccontextmanager
    async def update_invoice_line_item_handler(self) -> AsyncGenerator[UpdateInvoiceLineItemHandler, None]:
        """Dependency provider for UpdateInvoiceLineItemHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield UpdateInvoiceLineItemHandler(repo)

    @asynccontextmanager
    async def remove_invoice_line_item_handler(self) -> AsyncGenerator[RemoveInvoiceLineItemHandler, None]:
        """Dependency provider for RemoveInvoiceLineItemHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield RemoveInvoiceLineItemHandler(repo)

    @asynccontextmanager
    async def add_invoice_tax_line_handler(self) -> AsyncGenerator[AddInvoiceTaxLineHandler, None]:
        """Dependency provider for AddInvoiceTaxLineHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield AddInvoiceTaxLineHandler(repo)

    @asynccontextmanager
    async def remove_invoice_tax_line_handler(self) -> AsyncGenerator[RemoveInvoiceTaxLineHandler, None]:
        """Dependency provider for RemoveInvoiceTaxLineHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield RemoveInvoiceTaxLineHandler(repo)

    @asynccontextmanager
    async def add_invoice_adjustment_handler(self) -> AsyncGenerator[AddInvoiceAdjustmentHandler, None]:
        """Dependency provider for AddInvoiceAdjustmentHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield AddInvoiceAdjustmentHandler(repo)

    @asynccontextmanager
    async def remove_invoice_adjustment_handler(self) -> AsyncGenerator[RemoveInvoiceAdjustmentHandler, None]:
        """Dependency provider for RemoveInvoiceAdjustmentHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield RemoveInvoiceAdjustmentHandler(repo)

    @asynccontextmanager
    async def issue_invoice_handler(self) -> AsyncGenerator[IssueInvoiceHandler, None]:
        """Dependency provider for IssueInvoiceHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield IssueInvoiceHandler(repo)

    @asynccontextmanager
    async def void_invoice_handler(self) -> AsyncGenerator[VoidInvoiceHandler, None]:
        """Dependency provider for VoidInvoiceHandler use case."""
        async with session_manager.session() as session:
            repo = PostgresInvoiceRepository(session)
            yield VoidInvoiceHandler(repo)

    @asynccontextmanager
    async def create_payment_intent_handler(self) -> AsyncGenerator[CreatePaymentIntentHandler, None]:
        """Dependency provider for CreatePaymentIntentHandler use case."""
        async with session_manager.session() as session:
            invoice_repo = PostgresInvoiceRepository(session)
            intent_repo = PostgresPaymentIntentRepository(session)
            yield CreatePaymentIntentHandler(invoice_repo, intent_repo)

    @asynccontextmanager
    async def activate_payment_intent_handler(self) -> AsyncGenerator[ActivatePaymentIntentHandler, None]:
        """Dependency provider for ActivatePaymentIntentHandler use case."""
        async with session_manager.session() as session:
            intent_repo = PostgresPaymentIntentRepository(session)
            yield ActivatePaymentIntentHandler(intent_repo)

    @asynccontextmanager
    async def cancel_payment_intent_handler(self) -> AsyncGenerator[CancelPaymentIntentHandler, None]:
        """Dependency provider for CancelPaymentIntentHandler use case."""
        async with session_manager.session() as session:
            intent_repo = PostgresPaymentIntentRepository(session)
            yield CancelPaymentIntentHandler(intent_repo)

    @asynccontextmanager
    async def expire_payment_intent_handler(self) -> AsyncGenerator[ExpirePaymentIntentHandler, None]:
        """Dependency provider for ExpirePaymentIntentHandler use case."""
        async with session_manager.session() as session:
            intent_repo = PostgresPaymentIntentRepository(session)
            yield ExpirePaymentIntentHandler(intent_repo)

    @asynccontextmanager
    async def detect_payment_handler(self) -> AsyncGenerator[DetectPaymentHandler, None]:
        """Dependency provider for DetectPaymentHandler use case."""
        async with session_manager.session() as session:
            payment_repo = PostgresPaymentRepository(session)
            intent_repo = PostgresPaymentIntentRepository(session)
            yield DetectPaymentHandler(payment_repo, intent_repo)

    @asynccontextmanager
    async def start_payment_verification_handler(self) -> AsyncGenerator[StartPaymentVerificationHandler, None]:
        """Dependency provider for StartPaymentVerificationHandler use case."""
        async with session_manager.session() as session:
            payment_repo = PostgresPaymentRepository(session)
            intent_repo = PostgresPaymentIntentRepository(session)
            yield StartPaymentVerificationHandler(
                payment_repo,
                intent_repo,
                self.blockchain_provider,
                self.verification_service
            )

    @asynccontextmanager
    async def confirm_payment_handler(self) -> AsyncGenerator[ConfirmPaymentHandler, None]:
        """Dependency provider for ConfirmPaymentHandler use case."""
        async with session_manager.session() as session:
            payment_repo = PostgresPaymentRepository(session)
            yield ConfirmPaymentHandler(payment_repo)

    @asynccontextmanager
    async def reject_payment_handler(self) -> AsyncGenerator[RejectPaymentHandler, None]:
        """Dependency provider for RejectPaymentHandler use case."""
        async with session_manager.session() as session:
            payment_repo = PostgresPaymentRepository(session)
            yield RejectPaymentHandler(payment_repo)

    @asynccontextmanager
    async def reconcile_payment_handler(self) -> AsyncGenerator[ReconcilePaymentHandler, None]:
        """Dependency provider for ReconcilePaymentHandler use case."""
        async with session_manager.session() as session:
            payment_repo = PostgresPaymentRepository(session)
            intent_repo = PostgresPaymentIntentRepository(session)
            invoice_repo = PostgresInvoiceRepository(session)
            allocation_repo = PostgresPaymentAllocationRepository(session)
            yield ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)

    @asynccontextmanager
    async def create_receivable_handler(self) -> AsyncGenerator[CreateReceivableHandler, None]:
        """Dependency provider for CreateReceivableHandler use case."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            invoice_repo = PostgresInvoiceRepository(session)
            yield CreateReceivableHandler(receivable_repo, invoice_repo)

    @asynccontextmanager
    async def apply_payment_allocation_handler(self) -> AsyncGenerator[ApplyPaymentAllocationHandler, None]:
        """Dependency provider for ApplyPaymentAllocationHandler use case."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            allocation_repo = PostgresPaymentAllocationRepository(session)
            yield ApplyPaymentAllocationHandler(receivable_repo, allocation_repo)

    @asynccontextmanager
    async def allocate_payment_handler(self) -> AsyncGenerator[AllocatePaymentHandler, None]:
        """Dependency provider for AllocatePaymentHandler use case."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            allocation_repo = PostgresPaymentAllocationRepository(session)
            action_repo = PostgresCollectionActionRepository(session)
            yield AllocatePaymentHandler(receivable_repo, allocation_repo, action_repo)

    @asynccontextmanager
    async def mark_receivable_overdue_handler(self) -> AsyncGenerator[MarkReceivableOverdueHandler, None]:
        """Dependency provider for MarkReceivableOverdueHandler."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            yield MarkReceivableOverdueHandler(receivable_repo)

    @asynccontextmanager
    async def settle_receivable_handler(self) -> AsyncGenerator[SettleReceivableHandler, None]:
        """Dependency provider for SettleReceivableHandler."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            action_repo = PostgresCollectionActionRepository(session)
            yield SettleReceivableHandler(receivable_repo, action_repo)

    @asynccontextmanager
    async def write_off_receivable_handler(self) -> AsyncGenerator[WriteOffReceivableHandler, None]:
        """Dependency provider for WriteOffReceivableHandler."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            action_repo = PostgresCollectionActionRepository(session)
            yield WriteOffReceivableHandler(receivable_repo, action_repo)

    @asynccontextmanager
    async def advance_dunning_stage_handler(self) -> AsyncGenerator[AdvanceDunningStageHandler, None]:
        """Dependency provider for AdvanceDunningStageHandler."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            yield AdvanceDunningStageHandler(receivable_repo)

    @asynccontextmanager
    async def update_receivable_aging_handler(self) -> AsyncGenerator[UpdateReceivableAgingHandler, None]:
        """Dependency provider for UpdateReceivableAgingHandler."""
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            yield UpdateReceivableAgingHandler(receivable_repo)

    @asynccontextmanager
    async def receivable_query_service(self) -> AsyncGenerator[IReceivableQueryService, None]:
        """Dependency provider for read-only IReceivableQueryService."""
        async with session_manager.session() as session:
            yield PostgresReceivableQueryService(session)

    @asynccontextmanager
    async def create_settlement_handler(self) -> AsyncGenerator[CreateSettlementHandler, None]:
        """Dependency provider for CreateSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            payment_repo = PostgresPaymentRepository(session)
            availability_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
            yield CreateSettlementHandler(settlement_repo, payment_repo, availability_svc)

    @asynccontextmanager
    async def start_settlement_processing_handler(self) -> AsyncGenerator[StartSettlementProcessingHandler, None]:
        """Dependency provider for StartSettlementProcessingHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield StartSettlementProcessingHandler(settlement_repo)

    @asynccontextmanager
    async def submit_settlement_handler(self) -> AsyncGenerator[SubmitSettlementHandler, None]:
        """Dependency provider for SubmitSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield SubmitSettlementHandler(settlement_repo)

    @asynccontextmanager
    async def confirm_settlement_handler(self) -> AsyncGenerator[ConfirmSettlementHandler, None]:
        """Dependency provider for ConfirmSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield ConfirmSettlementHandler(settlement_repo)

    @asynccontextmanager
    async def complete_settlement_handler(self) -> AsyncGenerator[CompleteSettlementHandler, None]:
        """Dependency provider for CompleteSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield CompleteSettlementHandler(settlement_repo)

    @asynccontextmanager
    async def fail_settlement_handler(self) -> AsyncGenerator[FailSettlementHandler, None]:
        """Dependency provider for FailSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield FailSettlementHandler(settlement_repo)

    @asynccontextmanager
    async def retry_settlement_handler(self) -> AsyncGenerator[RetrySettlementHandler, None]:
        """Dependency provider for RetrySettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield RetrySettlementHandler(settlement_repo)

    @asynccontextmanager
    async def cancel_settlement_handler(self) -> AsyncGenerator[CancelSettlementHandler, None]:
        """Dependency provider for CancelSettlementHandler."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementRepository(session)
            yield CancelSettlementHandler(settlement_repo)

    @asynccontextmanager
    async def create_settlement_reconciliation_handler(self) -> AsyncGenerator[CreateSettlementReconciliationHandler, None]:
        """Dependency provider for CreateSettlementReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield CreateSettlementReconciliationHandler(repo)

    @asynccontextmanager
    async def open_settlement_reconciliation_handler(self) -> AsyncGenerator[OpenSettlementReconciliationHandler, None]:
        """Dependency provider for OpenSettlementReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield OpenSettlementReconciliationHandler(repo)

    @asynccontextmanager
    async def add_settlement_entry_reconciliation_handler(self) -> AsyncGenerator[AddSettlementEntryReconciliationHandler, None]:
        """Dependency provider for AddSettlementEntryReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield AddSettlementEntryReconciliationHandler(repo)

    @asynccontextmanager
    async def add_settlement_adjustment_reconciliation_handler(self) -> AsyncGenerator[AddSettlementAdjustmentReconciliationHandler, None]:
        """Dependency provider for AddSettlementAdjustmentReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield AddSettlementAdjustmentReconciliationHandler(repo)

    @asynccontextmanager
    async def begin_reconciliation_handler(self) -> AsyncGenerator[BeginReconciliationHandler, None]:
        """Dependency provider for BeginReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield BeginReconciliationHandler(repo)

    @asynccontextmanager
    async def reconcile_settlement_handler(self) -> AsyncGenerator[ReconcileSettlementHandler, None]:
        """Dependency provider for ReconcileSettlementHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield ReconcileSettlementHandler(repo)

    @asynccontextmanager
    async def record_settlement_discrepancy_handler(self) -> AsyncGenerator[RecordSettlementDiscrepancyHandler, None]:
        """Dependency provider for RecordSettlementDiscrepancyHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield RecordSettlementDiscrepancyHandler(repo)

    @asynccontextmanager
    async def resolve_settlement_discrepancy_handler(self) -> AsyncGenerator[ResolveSettlementDiscrepancyHandler, None]:
        """Dependency provider for ResolveSettlementDiscrepancyHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield ResolveSettlementDiscrepancyHandler(repo)

    @asynccontextmanager
    async def complete_settlement_reconciliation_handler(self) -> AsyncGenerator[CompleteSettlementReconciliationHandler, None]:
        """Dependency provider for CompleteSettlementReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield CompleteSettlementReconciliationHandler(repo)

    @asynccontextmanager
    async def cancel_settlement_reconciliation_handler(self) -> AsyncGenerator[CancelSettlementReconciliationHandler, None]:
        """Dependency provider for CancelSettlementReconciliationHandler."""
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            yield CancelSettlementReconciliationHandler(repo)

    @asynccontextmanager
    async def settlement_worker(self) -> AsyncGenerator[SettlementWorker, None]:
        """Dependency provider for SettlementWorker."""
        yield SettlementWorker(self.settlement_execution_adapter)

    @asynccontextmanager
    async def settlement_verification_worker(self) -> AsyncGenerator[SettlementVerificationWorker, None]:
        """Dependency provider for SettlementVerificationWorker."""
        yield SettlementVerificationWorker(self.settlement_verification_adapter, self.settlement_verification_service)

    @asynccontextmanager
    async def create_notification_handler(self) -> AsyncGenerator[CreateNotificationHandler, None]:
        """Dependency provider for CreateNotificationHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield CreateNotificationHandler(repo)

    @asynccontextmanager
    async def start_notification_processing_handler(self) -> AsyncGenerator[StartNotificationProcessingHandler, None]:
        """Dependency provider for StartNotificationProcessingHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield StartNotificationProcessingHandler(repo)

    @asynccontextmanager
    async def mark_notification_delivered_handler(self) -> AsyncGenerator[MarkNotificationDeliveredHandler, None]:
        """Dependency provider for MarkNotificationDeliveredHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield MarkNotificationDeliveredHandler(repo)

    @asynccontextmanager
    async def mark_notification_failed_handler(self) -> AsyncGenerator[MarkNotificationFailedHandler, None]:
        """Dependency provider for MarkNotificationFailedHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield MarkNotificationFailedHandler(repo)

    @asynccontextmanager
    async def retry_notification_handler(self) -> AsyncGenerator[RetryNotificationHandler, None]:
        """Dependency provider for RetryNotificationHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield RetryNotificationHandler(repo)

    @asynccontextmanager
    async def cancel_notification_handler(self) -> AsyncGenerator[CancelNotificationHandler, None]:
        """Dependency provider for CancelNotificationHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield CancelNotificationHandler(repo)

    @asynccontextmanager
    async def notification_worker(self) -> AsyncGenerator[NotificationWorker, None]:
        """Dependency provider for NotificationWorker."""
        yield self._notification_worker

    @asynccontextmanager
    async def notification_query_service(self) -> AsyncGenerator[INotificationQueryService, None]:
        """Dependency provider for read-only INotificationQueryService."""
        async with session_manager.session() as session:
            yield PostgresNotificationQueryService(session)

    @asynccontextmanager
    async def notification_template_repository(self) -> AsyncGenerator[PostgresNotificationTemplateRepository, None]:
        """Dependency provider for PostgresNotificationTemplateRepository."""
        async with session_manager.session() as session:
            yield PostgresNotificationTemplateRepository(session)

    @asynccontextmanager
    async def notification_template_service(self) -> AsyncGenerator[NotificationTemplateService, None]:
        """Dependency provider for NotificationTemplateService."""
        async with session_manager.session() as session:
            repo = PostgresNotificationTemplateRepository(session)
            yield NotificationTemplateService(repo)

    @asynccontextmanager
    async def notification_preference_repository(self) -> AsyncGenerator[PostgresNotificationPreferenceRepository, None]:
        """Dependency provider for PostgresNotificationPreferenceRepository."""
        async with session_manager.session() as session:
            yield PostgresNotificationPreferenceRepository(session)

    @asynccontextmanager
    async def recipient_resolver(self) -> AsyncGenerator[IRecipientResolver, None]:
        """Dependency provider for RecipientResolver."""
        yield RecipientResolver()

    @asynccontextmanager
    async def notification_event_worker(self) -> AsyncGenerator[NotificationEventWorker, None]:
        """Dependency provider for NotificationEventWorker."""
        async with session_manager.session() as session:
            pref_repo = PostgresNotificationPreferenceRepository(session)
            temp_repo = PostgresNotificationTemplateRepository(session)
            temp_svc = NotificationTemplateService(temp_repo)
            resolver = RecipientResolver()
            yield NotificationEventWorker(
                recipient_resolver=resolver,
                template_service=temp_svc,
                preference_repo=pref_repo
            )

    @asynccontextmanager
    async def replay_notification_handler(self) -> AsyncGenerator[ReplayNotificationHandler, None]:
        """Dependency provider for ReplayNotificationHandler."""
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            yield ReplayNotificationHandler(repo)

    @asynccontextmanager
    async def workflow_repository(self) -> AsyncGenerator[IWorkflowRepository, None]:
        """Dependency provider for PostgresWorkflowRepository."""
        async with session_manager.session() as session:
            yield PostgresWorkflowRepository(session)

    @asynccontextmanager
    async def receivable_repository(self) -> AsyncGenerator[IReceivableRepository, None]:
        """Dependency provider for PostgresReceivableRepository."""
        async with session_manager.session() as session:
            yield PostgresReceivableRepository(session)

    @asynccontextmanager
    async def payment_allocation_repository(self) -> AsyncGenerator[PostgresPaymentAllocationRepository, None]:
        """Dependency provider for PostgresPaymentAllocationRepository."""
        async with session_manager.session() as session:
            yield PostgresPaymentAllocationRepository(session)

    @asynccontextmanager
    async def create_workflow_handler(self) -> AsyncGenerator[CreateWorkflowHandler, None]:
        """Dependency provider for CreateWorkflowHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield CreateWorkflowHandler(repo)

    @asynccontextmanager
    async def create_workflow_version_handler(self) -> AsyncGenerator[CreateWorkflowVersionHandler, None]:
        """Dependency provider for CreateWorkflowVersionHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield CreateWorkflowVersionHandler(repo)

    @asynccontextmanager
    async def activate_workflow_handler(self) -> AsyncGenerator[ActivateWorkflowHandler, None]:
        """Dependency provider for ActivateWorkflowHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield ActivateWorkflowHandler(repo)

    @asynccontextmanager
    async def pause_workflow_handler(self) -> AsyncGenerator[PauseWorkflowHandler, None]:
        """Dependency provider for PauseWorkflowHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield PauseWorkflowHandler(repo)

    @asynccontextmanager
    async def archive_workflow_handler(self) -> AsyncGenerator[ArchiveWorkflowHandler, None]:
        """Dependency provider for ArchiveWorkflowHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield ArchiveWorkflowHandler(repo)

    @asynccontextmanager
    async def activate_workflow_version_handler(self) -> AsyncGenerator[ActivateWorkflowVersionHandler, None]:
        """Dependency provider for ActivateWorkflowVersionHandler."""
        async with session_manager.session() as session:
            repo = PostgresWorkflowRepository(session)
            yield ActivateWorkflowVersionHandler(repo)

    @asynccontextmanager
    async def workflow_query_service(self) -> AsyncGenerator[IWorkflowQueryService, None]:
        """Dependency provider for PostgresWorkflowQueryService."""
        async with session_manager.session() as session:
            yield PostgresWorkflowQueryService(session)

    @asynccontextmanager
    async def workflow_worker(self) -> AsyncGenerator[WorkflowWorker, None]:
        """Dependency provider for WorkflowWorker."""
        yield WorkflowWorker()

    @asynccontextmanager
    async def collection_action_repository(self) -> AsyncGenerator[ICollectionActionRepository, None]:
        """Dependency provider for ICollectionActionRepository."""
        async with session_manager.session() as session:
            yield PostgresCollectionActionRepository(session)

    @asynccontextmanager
    async def dunning_policy(self) -> AsyncGenerator[DunningPolicy, None]:
        """Dependency provider for DunningPolicy."""
        yield DunningPolicy()

    @asynccontextmanager
    async def dunning_worker(self) -> AsyncGenerator[DunningWorker, None]:
        """Dependency provider for DunningWorker."""
        yield DunningWorker(DunningPolicy())

    @asynccontextmanager
    async def collection_action_worker(self) -> AsyncGenerator[CollectionActionWorker, None]:
        """Dependency provider for CollectionActionWorker."""
        async with session_manager.session() as session:
            pref_repo = PostgresNotificationPreferenceRepository(session)
            resolver = RecipientResolver()
            tmpl_repo = PostgresNotificationTemplateRepository(session)
            tmpl_svc = NotificationTemplateService(tmpl_repo)
            yield CollectionActionWorker(resolver, tmpl_svc)

    @asynccontextmanager
    async def ar_event_processor(self) -> AsyncGenerator[AREventProcessor, None]:
        """Dependency provider for AREventProcessor."""
        yield AREventProcessor()

    @asynccontextmanager
    async def settlement_reconciliation_query_service(self) -> AsyncGenerator[PostgresSettlementQueryService, None]:
        """Dependency provider for PostgresSettlementQueryService."""
        async with session_manager.session() as session:
            yield PostgresSettlementQueryService(session)

    @asynccontextmanager
    async def financial_consistency_query_service(self) -> AsyncGenerator[IFinancialConsistencyQueryService, None]:
        """Dependency provider for PostgresFinancialConsistencyQueryService."""
        async with session_manager.session() as session:
            yield PostgresFinancialConsistencyQueryService(session)

    @asynccontextmanager
    async def financial_recovery_service(self) -> AsyncGenerator[IFinancialRecoveryService, None]:
        """Dependency provider for PostgresFinancialRecoveryService."""
        async with session_manager.session() as session:
            consistency_svc = PostgresFinancialConsistencyQueryService(session)
            yield PostgresFinancialRecoveryService(session, consistency_svc)

    @asynccontextmanager
    async def financial_consistency_automation_worker(self) -> AsyncGenerator[FinancialConsistencyAutomationWorker, None]:
        """Dependency provider for FinancialConsistencyAutomationWorker."""
        async with session_manager.session() as session:
            consistency_svc = PostgresFinancialConsistencyQueryService(session)
            recovery_svc = PostgresFinancialRecoveryService(session, consistency_svc)
            yield FinancialConsistencyAutomationWorker(consistency_svc, recovery_svc)

    @asynccontextmanager
    async def telemetry_service(self) -> AsyncGenerator[TelemetryService, None]:
        """Dependency provider for TelemetryService."""
        async with session_manager.session() as session:
            yield TelemetryService(session)

    @asynccontextmanager
    async def observability_query_service(self) -> AsyncGenerator[IObservabilityQueryService, None]:
        """Dependency provider for PostgresObservabilityQueryService."""
        async with session_manager.session() as session:
            yield PostgresObservabilityQueryService(session)

    @asynccontextmanager
    async def collection_action_query_service(self) -> AsyncGenerator[ICollectionActionQueryService, None]:
        """Dependency provider for read-only ICollectionActionQueryService."""
        async with session_manager.session() as session:
            yield PostgresCollectionActionQueryService(session)

    @asynccontextmanager
    async def source_transaction_repository(self) -> AsyncGenerator[PostgresSourceTransactionRepository, None]:
        """Dependency provider for PostgresSourceTransactionRepository."""
        async with session_manager.session() as session:
            yield PostgresSourceTransactionRepository(session)

    @asynccontextmanager
    async def settlement_reconciliation_automation_service(self) -> AsyncGenerator[ReconciliationAutomationService, None]:
        """Dependency provider for ReconciliationAutomationService."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementReconciliationRepository(session)
            source_tx_repo = PostgresSourceTransactionRepository(session)
            yield ReconciliationAutomationService(settlement_repo, source_tx_repo)

    @asynccontextmanager
    async def settlement_automation_worker(self) -> AsyncGenerator[SettlementAutomationWorker, None]:
        """Dependency provider for SettlementAutomationWorker."""
        async with session_manager.session() as session:
            settlement_repo = PostgresSettlementReconciliationRepository(session)
            source_tx_repo = PostgresSourceTransactionRepository(session)
            service = ReconciliationAutomationService(settlement_repo, source_tx_repo)
            yield SettlementAutomationWorker(service)

    @asynccontextmanager
    async def settlement_event_processor(self) -> AsyncGenerator[SettlementEventProcessor, None]:
        """Dependency provider for SettlementEventProcessor."""
        yield SettlementEventProcessor()


# Global container instance
container = Container()
