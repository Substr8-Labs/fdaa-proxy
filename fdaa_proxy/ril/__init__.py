"""
Runtime Integrity Layer (RIL)

Two-tier memory architecture:
- Hot tier: RIL Ledger (SQLite) - execution telemetry, crash recovery
- Cold tier: GAM (Git) - curated agent memory, searchable, verifiable

Components:
- CIA: Context Integrity Adapter (validates/repairs message contexts)
- Ledger: Execution state tracking with canonical IDs
- Triggers: Event-driven memory capture
- Promotion: RIL → GAM promotion pipeline
- Middleware: FastAPI integration

Architecture:
    Request → CIA → Trigger → RIL Ledger (hot, 24-48h)
                                  ↓
                        [Promotion Worker]
                                  ↓
                           GAM Repo (cold, permanent)
                                  ↓
                           DCT Receipt (proof)
"""

from .cia import (
    ContextIntegrityAdapter,
    RepairMode,
    RepairResult,
    ValidationResult,
)

from .ledger_v2 import (
    WorkLedgerV2,
    Event,
    EventType,
    ToolTransaction,
    ToolTxnStatus,
    WorkItem,
    WorkStatus,
    PromotionState,
    make_turn_id,
    make_event_id,
    make_tool_txn_id,
    hash_payload,
    migrate_v1_to_v2,
)

from .triggers import (
    TriggerEngine,
    TriggerEvent,
    TriggerContext,
    TriggerResult,
)

from .promotion import (
    PromotionWorker,
    PromotionRule,
    GAMRepo,
    GAMRepoConfig,
    should_promote,
    render_event_artifact,
    write_dct_receipt,
    create_promotion_worker,
)

from .middleware import (
    RILMiddleware,
    RILState,
    RILConfig,
    setup_ril,
    create_ril_state,
)

__all__ = [
    # CIA
    "ContextIntegrityAdapter",
    "RepairMode",
    "RepairResult",
    "ValidationResult",
    # Ledger v2
    "WorkLedgerV2",
    "Event",
    "EventType",
    "ToolTransaction",
    "ToolTxnStatus",
    "WorkItem",
    "WorkStatus",
    "PromotionState",
    "make_turn_id",
    "make_event_id",
    "make_tool_txn_id",
    "hash_payload",
    "migrate_v1_to_v2",
    # Triggers
    "TriggerEngine",
    "TriggerEvent",
    "TriggerContext",
    "TriggerResult",
    # Promotion
    "PromotionWorker",
    "PromotionRule",
    "GAMRepo",
    "GAMRepoConfig",
    "should_promote",
    "render_event_artifact",
    "write_dct_receipt",
    "create_promotion_worker",
    # Middleware
    "RILMiddleware",
    "RILState",
    "RILConfig",
    "setup_ril",
    "create_ril_state",
]
