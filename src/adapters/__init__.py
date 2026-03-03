"""FDAA Proxy Adapters."""

from .context_integrity import (
    ContextIntegrityAdapter,
    CIAConfig,
    Mode,
    ValidationResult,
    IntegrityViolation,
    IntegrityEvent,
    ContextIntegrityError,
    validate_tool_pairing,
    repair_context,
    find_safe_truncation_point,
    has_unresolved_tool_uses,
    create_adapter,
)

__all__ = [
    "ContextIntegrityAdapter",
    "CIAConfig",
    "Mode",
    "ValidationResult",
    "IntegrityViolation",
    "IntegrityEvent",
    "ContextIntegrityError",
    "validate_tool_pairing",
    "repair_context",
    "find_safe_truncation_point",
    "has_unresolved_tool_uses",
    "create_adapter",
]
