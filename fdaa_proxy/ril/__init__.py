"""
Runtime Integrity Layer (RIL) - Production Middleware

Provides automatic integrity enforcement for all proxy requests:
- CIA: Context Integrity Adapter (validates tool pairing)
- Triggers: Event-driven memory capture
- Ledger: Crash-resilient execution state
"""

from .middleware import RILMiddleware, RILConfig, setup_ril
from .cia import ContextIntegrityAdapter, RepairMode, ValidationResult
from .triggers import TriggerEngine, TriggerEvent
from .ledger import WorkLedger, WorkItem, WorkStatus

__all__ = [
    "RILMiddleware",
    "RILConfig",
    "ContextIntegrityAdapter",
    "RepairMode",
    "ValidationResult",
    "TriggerEngine",
    "TriggerEvent",
    "WorkLedger",
    "WorkItem",
    "WorkStatus",
]
