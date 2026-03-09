"""
Substr8 RIL - Runtime Integrity Layer

The middleware that makes agent execution trustworthy:
- CIA (Context Integrity Adapter) - structural validation
- GAM Triggers - automatic memory capture
- Work Ledger - crash-resilient execution state

RIL transforms LLM interaction from best-effort conversation
into a governed execution system.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any, List


class RepairMode(str, Enum):
    """CIA repair modes."""
    STRICT = "strict"        # Reject invalid payloads
    PERMISSIVE = "permissive"  # Repair and log
    FORENSIC = "forensic"    # Halt and snapshot


class TriggerEvent(str, Enum):
    """GAM trigger events."""
    MESSAGE_RECEIVED = "message_received"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"
    TURN_COMPLETED = "turn_completed"
    DECISION_POINT = "decision_point"
    CRASH_RECOVERY = "crash_recovery"


@dataclass
class ValidationResult:
    """Result of context validation."""
    valid: bool
    errors: List[str]
    warnings: List[str]
    repairs_needed: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "repairs_needed": self.repairs_needed,
        }


@dataclass 
class RepairResult:
    """Result of context repair."""
    success: bool
    original_hash: str
    repaired_hash: str
    repairs_applied: List[Dict[str, Any]]
    mode: RepairMode
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "original_hash": self.original_hash,
            "repaired_hash": self.repaired_hash,
            "repairs_applied": self.repairs_applied,
            "mode": self.mode.value,
        }
