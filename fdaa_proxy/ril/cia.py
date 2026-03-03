"""
Context Integrity Adapter (CIA)

Validates and repairs tool_use/tool_result pairing in message arrays.
Runs automatically on every request as middleware.

Repair Modes:
- STRICT: Reject invalid payloads (no repair)
- PERMISSIVE: Repair and log (default for production)
- FORENSIC: Halt and snapshot for debugging
"""

import json
import hashlib
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("fdaa-ril-cia")


class RepairMode(str, Enum):
    STRICT = "strict"
    PERMISSIVE = "permissive"
    FORENSIC = "forensic"


@dataclass
class ValidationResult:
    """Result of context validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    repairs_needed: List[Dict[str, Any]] = field(default_factory=list)
    
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
    repairs_applied: List[Dict[str, Any]] = field(default_factory=list)
    mode: RepairMode = RepairMode.PERMISSIVE
    timestamp: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "original_hash": self.original_hash,
            "repaired_hash": self.repaired_hash,
            "repairs_applied": self.repairs_applied,
            "mode": self.mode.value,
            "timestamp": self.timestamp,
        }


class ContextIntegrityAdapter:
    """
    Validates and repairs context integrity for LLM API calls.
    
    Key validations:
    1. Every tool_result must reference an existing tool_use
    2. No duplicate tool_use IDs
    3. Unresolved tool_uses get synthetic failure injection
    """
    
    def __init__(self, mode: RepairMode = RepairMode.PERMISSIVE):
        self.mode = mode
        self.stats = {
            "total_validated": 0,
            "valid": 0,
            "repaired": 0,
            "rejected": 0,
        }
    
    def validate(self, messages: List[Dict[str, Any]]) -> ValidationResult:
        """
        Validate tool_use/tool_result pairing in a message array.
        
        Returns ValidationResult with errors, warnings, and needed repairs.
        """
        errors = []
        warnings = []
        repairs_needed = []
        
        # Track tool_use blocks by ID
        tool_uses: Dict[str, Dict[str, Any]] = {}
        tool_results: Dict[str, Dict[str, Any]] = {}
        
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", [])
            
            if not isinstance(content, list):
                continue
            
            for j, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                    
                block_type = block.get("type", "")
                
                if block_type == "tool_use":
                    tool_id = block.get("id", "")
                    if not tool_id:
                        errors.append(f"Message {i}, block {j}: tool_use missing id")
                        continue
                    
                    if tool_id in tool_uses:
                        errors.append(f"Message {i}, block {j}: duplicate tool_use id '{tool_id}'")
                    else:
                        tool_uses[tool_id] = {
                            "message_idx": i,
                            "block_idx": j,
                            "name": block.get("name", "unknown"),
                        }
                
                elif block_type == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    if not tool_use_id:
                        errors.append(f"Message {i}, block {j}: tool_result missing tool_use_id")
                        repairs_needed.append({
                            "type": "orphan_tool_result",
                            "message_idx": i,
                            "block_idx": j,
                            "action": "remove",
                        })
                        continue
                    
                    if tool_use_id not in tool_uses:
                        errors.append(
                            f"Message {i}, block {j}: orphaned tool_result "
                            f"references non-existent tool_use '{tool_use_id}'"
                        )
                        repairs_needed.append({
                            "type": "orphan_tool_result",
                            "message_idx": i,
                            "block_idx": j,
                            "tool_use_id": tool_use_id,
                            "action": "remove",
                        })
                    else:
                        tool_results[tool_use_id] = {
                            "message_idx": i,
                            "block_idx": j,
                        }
        
        # Check for unresolved tool_uses (no matching result)
        for tool_id, info in tool_uses.items():
            if tool_id not in tool_results:
                warnings.append(
                    f"Unresolved tool_use '{tool_id}' ({info['name']}) "
                    f"at message {info['message_idx']}"
                )
                repairs_needed.append({
                    "type": "unresolved_tool_use",
                    "tool_use_id": tool_id,
                    "message_idx": info["message_idx"],
                    "block_idx": info["block_idx"],
                    "tool_name": info["name"],
                    "action": "inject_synthetic_failure",
                })
        
        self.stats["total_validated"] += 1
        if len(errors) == 0:
            self.stats["valid"] += 1
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            repairs_needed=repairs_needed,
        )
    
    def repair(
        self, 
        messages: List[Dict[str, Any]],
        validation: Optional[ValidationResult] = None,
    ) -> Tuple[List[Dict[str, Any]], RepairResult]:
        """
        Repair a corrupted context based on validation results.
        
        Returns (repaired_messages, RepairResult).
        """
        import copy
        
        # Hash original
        original_json = json.dumps(messages, sort_keys=True)
        original_hash = f"sha256:{hashlib.sha256(original_json.encode()).hexdigest()}"
        
        # Validate if not provided
        if validation is None:
            validation = self.validate(messages)
        
        # Already valid
        if validation.valid and not validation.warnings:
            return messages, RepairResult(
                success=True,
                original_hash=original_hash,
                repaired_hash=original_hash,
                repairs_applied=[],
                mode=self.mode,
            )
        
        # Strict mode: reject
        if self.mode == RepairMode.STRICT:
            self.stats["rejected"] += 1
            return messages, RepairResult(
                success=False,
                original_hash=original_hash,
                repaired_hash=original_hash,
                repairs_applied=[],
                mode=self.mode,
            )
        
        # Forensic mode: log and halt (for debugging)
        if self.mode == RepairMode.FORENSIC:
            logger.warning(f"FORENSIC: Context integrity violation detected")
            logger.warning(f"Errors: {validation.errors}")
            logger.warning(f"Original hash: {original_hash}")
            # In forensic mode, we still repair but log extensively
        
        # Apply repairs
        repaired = copy.deepcopy(messages)
        repairs_applied = []
        
        # Sort repairs by message index (descending) to avoid index shifting
        repairs = sorted(
            validation.repairs_needed, 
            key=lambda r: (-r.get("message_idx", 0), -r.get("block_idx", 0))
        )
        
        for repair in repairs:
            repair_type = repair.get("type", "")
            
            if repair_type == "orphan_tool_result" and repair.get("action") == "remove":
                msg_idx = repair["message_idx"]
                block_idx = repair["block_idx"]
                
                if msg_idx < len(repaired):
                    content = repaired[msg_idx].get("content", [])
                    if isinstance(content, list) and block_idx < len(content):
                        removed = content.pop(block_idx)
                        repairs_applied.append({
                            "type": "removed_orphan_tool_result",
                            "tool_use_id": repair.get("tool_use_id", "unknown"),
                            "message_idx": msg_idx,
                        })
                        logger.info(f"CIA: Removed orphan tool_result at [{msg_idx}][{block_idx}]")
            
            elif repair_type == "unresolved_tool_use" and repair.get("action") == "inject_synthetic_failure":
                tool_use_id = repair["tool_use_id"]
                tool_name = repair.get("tool_name", "unknown")
                inject_after = repair["message_idx"]
                
                # Create synthetic tool_result with error
                synthetic_result = {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "content": (
                            f"[RIL] Tool execution interrupted. "
                            f"Context was recovered after system disruption. "
                            f"Tool '{tool_name}' did not complete."
                        ),
                    }]
                }
                
                # Insert after the tool_use message
                repaired.insert(inject_after + 1, synthetic_result)
                repairs_applied.append({
                    "type": "injected_synthetic_failure",
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "injected_at": inject_after + 1,
                })
                logger.info(f"CIA: Injected synthetic failure for tool_use '{tool_use_id}'")
        
        # Hash repaired
        repaired_json = json.dumps(repaired, sort_keys=True)
        repaired_hash = f"sha256:{hashlib.sha256(repaired_json.encode()).hexdigest()}"
        
        self.stats["repaired"] += 1
        
        return repaired, RepairResult(
            success=True,
            original_hash=original_hash,
            repaired_hash=repaired_hash,
            repairs_applied=repairs_applied,
            mode=self.mode,
        )
    
    def process(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], RepairResult]:
        """
        Validate and repair in one call. Main entry point for middleware.
        """
        validation = self.validate(messages)
        return self.repair(messages, validation)
    
    def get_stats(self) -> Dict[str, int]:
        """Get validation/repair statistics."""
        return self.stats.copy()
