"""
Context Integrity Adapter (CIA) for FDAA Proxy.

Validates and repairs tool_use/tool_result pairing in LLM conversation histories.
Prevents corrupted contexts from reaching upstream APIs.

Reference: docs/architecture/CONTEXT-INTEGRITY-ADAPTER.md
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, TypedDict

logger = logging.getLogger(__name__)


# =============================================================================
# Type Definitions
# =============================================================================

class ContentBlock(TypedDict, total=False):
    """A content block within a message."""
    type: str
    id: str  # For tool_use
    tool_use_id: str  # For tool_result
    name: str  # Tool name for tool_use
    input: dict[str, Any]  # Tool input for tool_use
    content: str | list[Any]  # Result content
    is_error: bool  # For tool_result


class Message(TypedDict, total=False):
    """An LLM conversation message."""
    role: str  # "user" | "assistant" | "system"
    content: str | list[ContentBlock]


class IntegrityViolation(TypedDict):
    """A detected integrity violation."""
    type: str
    message_index: int
    tool_use_id: str | None
    details: str
    severity: str  # "error" | "warning"


class IntegrityEvent(TypedDict):
    """An event to log to DCT."""
    event: str
    timestamp: str
    session_id: str | None
    action: str
    tool_use_id: str | None
    reason: str
    message_index: int


class DCTLogger(Protocol):
    """Protocol for DCT logging integration."""
    
    def log_event(self, event: IntegrityEvent) -> None:
        """Log an integrity event to DCT."""
        ...


class Mode(Enum):
    """CIA operating modes."""
    STRICT = "strict"      # Reject on any violation
    REPAIR = "repair"      # Auto-repair violations
    LOG_ONLY = "log-only"  # Log but don't modify/reject


@dataclass
class CIAConfig:
    """Configuration for Context Integrity Adapter."""
    mode: Mode = Mode.REPAIR
    
    # Validation flags
    validate_tool_result_references: bool = True
    validate_dangling_tool_uses: bool = True
    validate_message_ordering: bool = True
    
    # Repair flags (only apply in REPAIR mode)
    prune_orphans: bool = True
    inject_synthetic_failures: bool = True
    
    # Truncation
    respect_tool_boundaries: bool = True
    
    # Logging
    dct_enabled: bool = True
    log_level: str = "warn"


@dataclass
class ValidationResult:
    """Result of context validation."""
    valid: bool
    violations: list[IntegrityViolation] = field(default_factory=list)
    repaired_messages: list[Message] | None = None
    events: list[IntegrityEvent] = field(default_factory=list)


# =============================================================================
# Utility Functions
# =============================================================================

def _get_content_blocks(message: Message) -> list[ContentBlock]:
    """Extract content blocks from a message, normalizing string content."""
    content = message.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "content": content}]
    return list(content) if content else []


def _extract_tool_use_ids(message: Message) -> set[str]:
    """Extract all tool_use IDs from an assistant message."""
    ids: set[str] = set()
    for block in _get_content_blocks(message):
        if block.get("type") == "tool_use" and "id" in block:
            ids.add(block["id"])
    return ids


def _extract_tool_result_ids(message: Message) -> set[str]:
    """Extract all tool_use_ids referenced by tool_results in a user message."""
    ids: set[str] = set()
    for block in _get_content_blocks(message):
        if block.get("type") == "tool_result" and "tool_use_id" in block:
            ids.add(block["tool_use_id"])
    return ids


def _has_tool_results(message: Message) -> bool:
    """Check if message contains any tool_result blocks."""
    return any(
        block.get("type") == "tool_result" 
        for block in _get_content_blocks(message)
    )


def _has_tool_uses(message: Message) -> bool:
    """Check if message contains any tool_use blocks."""
    return any(
        block.get("type") == "tool_use" 
        for block in _get_content_blocks(message)
    )


def _estimate_tokens(message: Message) -> int:
    """
    Rough token estimation for a message.
    
    Uses ~4 chars per token heuristic. For production, integrate
    with actual tokenizer (tiktoken, anthropic-tokenizer, etc.)
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content) // 4 + 1
    
    # Sum up content from all blocks
    total_chars = 0
    for block in content:
        if "content" in block:
            block_content = block["content"]
            if isinstance(block_content, str):
                total_chars += len(block_content)
            elif isinstance(block_content, list):
                total_chars += sum(
                    len(str(item)) for item in block_content
                )
        if "input" in block:
            total_chars += len(str(block["input"]))
        if "name" in block:
            total_chars += len(block["name"])
    
    return total_chars // 4 + 1


def _now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Core Validation Functions
# =============================================================================

def validate_tool_pairing(messages: list[Message]) -> list[IntegrityViolation]:
    """
    Validate tool_use/tool_result pairing integrity.
    
    Checks:
    1. Every tool_result references a tool_use in the previous message
    2. Every tool_use (except in the last message) has a corresponding tool_result
    3. Messages alternate correctly (user/assistant)
    
    Args:
        messages: List of conversation messages
        
    Returns:
        List of detected integrity violations (empty if valid)
    """
    violations: list[IntegrityViolation] = []
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        
        # Rule 1: Check tool_result references
        if role == "user":
            tool_result_ids = _extract_tool_result_ids(msg)
            
            if tool_result_ids:
                # Must have a preceding assistant message with matching tool_uses
                if i == 0:
                    for tid in tool_result_ids:
                        violations.append({
                            "type": "orphaned_tool_result",
                            "message_index": i,
                            "tool_use_id": tid,
                            "details": "tool_result at start of conversation with no preceding tool_use",
                            "severity": "error"
                        })
                elif messages[i - 1].get("role") != "assistant":
                    for tid in tool_result_ids:
                        violations.append({
                            "type": "orphaned_tool_result",
                            "message_index": i,
                            "tool_use_id": tid,
                            "details": "tool_result not preceded by assistant message",
                            "severity": "error"
                        })
                else:
                    # Check each tool_result has matching tool_use
                    valid_tool_use_ids = _extract_tool_use_ids(messages[i - 1])
                    for tid in tool_result_ids:
                        if tid not in valid_tool_use_ids:
                            violations.append({
                                "type": "orphaned_tool_result",
                                "message_index": i,
                                "tool_use_id": tid,
                                "details": f"tool_result references non-existent tool_use_id: {tid}",
                                "severity": "error"
                            })
        
        # Rule 2: Check for dangling tool_uses (except last message)
        if role == "assistant" and i < len(messages) - 1:
            tool_use_ids = _extract_tool_use_ids(msg)
            
            if tool_use_ids:
                # Next message should be user with tool_results
                next_msg = messages[i + 1]
                if next_msg.get("role") != "user":
                    for tid in tool_use_ids:
                        violations.append({
                            "type": "dangling_tool_use",
                            "message_index": i,
                            "tool_use_id": tid,
                            "details": "tool_use not followed by user message",
                            "severity": "error"
                        })
                else:
                    resolved_ids = _extract_tool_result_ids(next_msg)
                    for tid in tool_use_ids:
                        if tid not in resolved_ids:
                            violations.append({
                                "type": "dangling_tool_use",
                                "message_index": i,
                                "tool_use_id": tid,
                                "details": f"tool_use {tid} has no matching tool_result",
                                "severity": "error"
                            })
        
        # Rule 4: Message ordering (basic alternation check)
        if i > 0:
            prev_role = messages[i - 1].get("role", "")
            # Allow system messages anywhere, but user/assistant should alternate
            if role in ("user", "assistant") and prev_role in ("user", "assistant"):
                if role == prev_role:
                    violations.append({
                        "type": "message_ordering",
                        "message_index": i,
                        "tool_use_id": None,
                        "details": f"Consecutive {role} messages at indices {i-1} and {i}",
                        "severity": "warning"
                    })
    
    return violations


# =============================================================================
# Repair Functions
# =============================================================================

def repair_context(
    messages: list[Message],
    *,
    prune_orphans: bool = True,
    inject_failures: bool = True,
    session_id: str | None = None,
) -> tuple[list[Message], list[IntegrityEvent]]:
    """
    Repair integrity violations in conversation history.
    
    Performs two repair strategies:
    1. Prune orphaned tool_results (those referencing non-existent tool_uses)
    2. Inject synthetic failure results for dangling tool_uses
    
    Args:
        messages: List of conversation messages (will be deep copied)
        prune_orphans: Whether to remove orphaned tool_results
        inject_failures: Whether to inject synthetic failures for dangling tool_uses
        session_id: Optional session ID for event logging
        
    Returns:
        Tuple of (repaired messages, list of repair events)
    """
    # Deep copy to avoid mutating input
    repaired = copy.deepcopy(messages)
    events: list[IntegrityEvent] = []
    
    if prune_orphans:
        repaired, prune_events = _prune_orphaned_tool_results(repaired, session_id)
        events.extend(prune_events)
    
    if inject_failures:
        repaired, inject_events = _resolve_dangling_tool_uses(repaired, session_id)
        events.extend(inject_events)
    
    return repaired, events


def _prune_orphaned_tool_results(
    messages: list[Message],
    session_id: str | None = None,
) -> tuple[list[Message], list[IntegrityEvent]]:
    """
    Remove tool_results that reference non-existent tool_use_ids.
    
    Modifies messages in-place and returns events for logging.
    """
    events: list[IntegrityEvent] = []
    
    for i, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        
        # Gather valid tool_use_ids from previous assistant message
        valid_tool_use_ids: set[str] = set()
        if i > 0 and messages[i - 1].get("role") == "assistant":
            valid_tool_use_ids = _extract_tool_use_ids(messages[i - 1])
        
        # Filter content blocks
        new_content: list[ContentBlock] = []
        for block in content:
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id not in valid_tool_use_ids:
                    # Log pruning event
                    events.append({
                        "event": "context_integrity_repair",
                        "timestamp": _now_iso(),
                        "session_id": session_id,
                        "action": "prune_orphaned_tool_result",
                        "tool_use_id": tool_use_id,
                        "reason": "No matching tool_use in previous message",
                        "message_index": i
                    })
                    logger.warning(
                        f"Pruned orphaned tool_result: {tool_use_id} at index {i}"
                    )
                    continue  # Skip this block
            
            new_content.append(block)
        
        msg["content"] = new_content
    
    # Remove empty user messages that only had tool_results
    messages = [
        msg for msg in messages
        if not (
            msg.get("role") == "user" 
            and isinstance(msg.get("content"), list) 
            and len(msg.get("content", [])) == 0
        )
    ]
    
    return messages, events


def _resolve_dangling_tool_uses(
    messages: list[Message],
    session_id: str | None = None,
) -> tuple[list[Message], list[IntegrityEvent]]:
    """
    Add synthetic failure results for tool_uses without results.
    
    Handles cases where tool execution was interrupted (gateway restart, etc.)
    """
    events: list[IntegrityEvent] = []
    
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        
        tool_use_ids = _extract_tool_use_ids(msg)
        if not tool_use_ids:
            continue
        
        # Skip if this is the last message (pending execution is valid)
        if i == len(messages) - 1:
            continue
        
        # Check next message for results
        if i + 1 < len(messages):
            next_msg = messages[i + 1]
            
            # If next message isn't a user message, we need to inject one
            if next_msg.get("role") != "user":
                # Insert a synthetic user message with failure results
                synthetic_results: list[ContentBlock] = []
                for tool_id in tool_use_ids:
                    synthetic_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": True,
                        "content": "[CIA] Tool execution interrupted - no result recorded"
                    })
                    events.append({
                        "event": "context_integrity_repair",
                        "timestamp": _now_iso(),
                        "session_id": session_id,
                        "action": "inject_synthetic_failure",
                        "tool_use_id": tool_id,
                        "reason": "No following user message for tool results",
                        "message_index": i
                    })
                    logger.warning(
                        f"Injected synthetic failure for tool_use: {tool_id} at index {i}"
                    )
                
                # Insert the synthetic message
                messages.insert(i + 1, {
                    "role": "user",
                    "content": synthetic_results
                })
            else:
                # Check which tool_uses are missing results
                resolved_ids = _extract_tool_result_ids(next_msg)
                dangling = tool_use_ids - resolved_ids
                
                if dangling:
                    # Ensure content is a list
                    content = next_msg.get("content", [])
                    if isinstance(content, str):
                        content = [{"type": "text", "content": content}]
                    
                    for tool_id in dangling:
                        content.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "is_error": True,
                            "content": "[CIA] Tool execution interrupted - no result recorded"
                        })
                        events.append({
                            "event": "context_integrity_repair",
                            "timestamp": _now_iso(),
                            "session_id": session_id,
                            "action": "inject_synthetic_failure",
                            "tool_use_id": tool_id,
                            "reason": "tool_use has no matching tool_result",
                            "message_index": i
                        })
                        logger.warning(
                            f"Injected synthetic failure for tool_use: {tool_id} at index {i}"
                        )
                    
                    next_msg["content"] = content
    
    return messages, events


# =============================================================================
# Truncation Functions
# =============================================================================

def find_safe_truncation_point(
    messages: list[Message],
    target_tokens: int,
    *,
    from_end: bool = True,
) -> int:
    """
    Find a safe index for truncating messages without splitting tool transactions.
    
    A tool transaction consists of:
    - An assistant message containing tool_use block(s)
    - A user message containing corresponding tool_result block(s)
    
    Truncation must preserve or remove entire transactions.
    
    Args:
        messages: List of conversation messages
        target_tokens: Target token count to keep
        from_end: If True, keep messages from the end (most recent).
                  If False, keep messages from the start.
                  
    Returns:
        Safe truncation index. When from_end=True, this is the first index
        to keep (messages[:index] can be removed). When from_end=False,
        this is the last index to keep (messages[index:] can be removed).
    """
    if not messages:
        return 0
    
    if from_end:
        return _find_truncation_from_end(messages, target_tokens)
    else:
        return _find_truncation_from_start(messages, target_tokens)


def _find_truncation_from_end(messages: list[Message], target_tokens: int) -> int:
    """Find safe truncation point, keeping messages from the end."""
    token_count = 0
    safe_index = len(messages)  # Start with keeping nothing (truncate everything)
    
    # Walk backwards through messages
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        token_count += _estimate_tokens(msg)
        
        # Check if we're at a safe boundary (not in middle of tool transaction)
        is_safe = True
        
        # If this is a user message with tool_results, we must include
        # the preceding assistant message with tool_uses
        if msg.get("role") == "user" and _has_tool_results(msg):
            # Must include previous message (should be assistant with tool_use)
            if i > 0:
                is_safe = False  # Can't stop here, need the tool_use too
        
        if is_safe:
            safe_index = i
        
        if token_count >= target_tokens:
            break
    
    return safe_index


def _find_truncation_from_start(messages: list[Message], target_tokens: int) -> int:
    """Find safe truncation point, keeping messages from the start."""
    token_count = 0
    safe_index = 0
    
    for i, msg in enumerate(messages):
        token_count += _estimate_tokens(msg)
        
        # Check if we're at a safe boundary
        is_safe = True
        
        # If this is an assistant message with tool_uses, we must include
        # the following user message with tool_results
        if msg.get("role") == "assistant" and _has_tool_uses(msg):
            # Must include next message (should be user with tool_result)
            if i < len(messages) - 1:
                is_safe = False  # Can't stop here, need the tool_result too
        
        if is_safe:
            safe_index = i + 1  # Can safely truncate after this message
        
        if token_count >= target_tokens:
            break
    
    return safe_index


def has_unresolved_tool_uses(messages: list[Message]) -> bool:
    """
    Check if message sequence has any unresolved tool_use blocks.
    
    Returns True if any tool_use lacks a corresponding tool_result.
    """
    pending_tool_ids: set[str] = set()
    
    for msg in messages:
        role = msg.get("role", "")
        
        if role == "assistant":
            pending_tool_ids.update(_extract_tool_use_ids(msg))
        elif role == "user":
            resolved = _extract_tool_result_ids(msg)
            pending_tool_ids -= resolved
    
    return len(pending_tool_ids) > 0


# =============================================================================
# Main Adapter Class
# =============================================================================

class ContextIntegrityAdapter:
    """
    Context Integrity Adapter for FDAA Proxy.
    
    Validates and repairs LLM conversation contexts to ensure
    tool_use/tool_result pairing integrity before forwarding to APIs.
    
    Example:
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.REPAIR),
            dct_logger=my_dct_client
        )
        
        result = adapter.validate(messages, session_id="abc123")
        
        if result.valid:
            # Safe to forward to API
            forward_to_api(result.repaired_messages or messages)
        else:
            # Handle validation failure
            raise ContextIntegrityError(result.violations)
    
    Attributes:
        config: CIA configuration options
        dct_logger: Optional DCT logger for audit trail
    """
    
    def __init__(
        self,
        config: CIAConfig | None = None,
        dct_logger: DCTLogger | Callable[[IntegrityEvent], None] | None = None,
    ):
        """
        Initialize the Context Integrity Adapter.
        
        Args:
            config: Configuration options. Defaults to Mode.REPAIR.
            dct_logger: Optional logger for DCT integration. Can be either:
                - An object with a `log_event` method
                - A callable that takes an IntegrityEvent
        """
        self.config = config or CIAConfig()
        self._dct_logger = dct_logger
    
    def validate(
        self,
        messages: list[Message],
        session_id: str | None = None,
    ) -> ValidationResult:
        """
        Validate and optionally repair message context.
        
        Based on the configured mode:
        - STRICT: Returns invalid result on any violation
        - REPAIR: Attempts to repair violations, returns repaired messages
        - LOG_ONLY: Logs violations but returns valid with original messages
        
        Args:
            messages: List of conversation messages
            session_id: Optional session ID for logging
            
        Returns:
            ValidationResult with validity status, violations, and optionally
            repaired messages.
        """
        # Run validation
        violations = validate_tool_pairing(messages)
        events: list[IntegrityEvent] = []
        repaired_messages: list[Message] | None = None
        
        if not violations:
            return ValidationResult(valid=True, violations=[])
        
        # Log violations
        for v in violations:
            logger.log(
                logging.WARNING if v["severity"] == "warning" else logging.ERROR,
                f"Context integrity violation: {v['type']} at index {v['message_index']}: {v['details']}"
            )
        
        # Handle based on mode
        if self.config.mode == Mode.STRICT:
            # Convert violations to events for logging
            for v in violations:
                event: IntegrityEvent = {
                    "event": "context_integrity_violation",
                    "timestamp": _now_iso(),
                    "session_id": session_id,
                    "action": "reject",
                    "tool_use_id": v.get("tool_use_id"),
                    "reason": v["details"],
                    "message_index": v["message_index"]
                }
                events.append(event)
                self._log_to_dct(event)
            
            return ValidationResult(
                valid=False,
                violations=violations,
                events=events
            )
        
        elif self.config.mode == Mode.REPAIR:
            repaired_messages, repair_events = repair_context(
                messages,
                prune_orphans=self.config.prune_orphans,
                inject_failures=self.config.inject_synthetic_failures,
                session_id=session_id,
            )
            events.extend(repair_events)
            
            # Log events to DCT
            for event in repair_events:
                self._log_to_dct(event)
            
            # Re-validate after repair
            remaining_violations = validate_tool_pairing(repaired_messages)
            
            return ValidationResult(
                valid=len(remaining_violations) == 0,
                violations=remaining_violations,
                repaired_messages=repaired_messages,
                events=events
            )
        
        else:  # LOG_ONLY
            for v in violations:
                event = {
                    "event": "context_integrity_violation",
                    "timestamp": _now_iso(),
                    "session_id": session_id,
                    "action": "log_only",
                    "tool_use_id": v.get("tool_use_id"),
                    "reason": v["details"],
                    "message_index": v["message_index"]
                }
                events.append(event)
                self._log_to_dct(event)
            
            return ValidationResult(
                valid=True,  # LOG_ONLY always returns valid
                violations=violations,
                repaired_messages=None,
                events=events
            )
    
    def find_safe_truncation(
        self,
        messages: list[Message],
        target_tokens: int,
        from_end: bool = True,
    ) -> int:
        """
        Find safe truncation point respecting tool transaction boundaries.
        
        Wrapper around find_safe_truncation_point that respects
        the respect_tool_boundaries config option.
        
        Args:
            messages: List of conversation messages
            target_tokens: Target token count to keep
            from_end: If True, keep most recent messages
            
        Returns:
            Safe truncation index
        """
        if not self.config.respect_tool_boundaries:
            # Simple truncation without boundary respect
            token_count = 0
            if from_end:
                for i in range(len(messages) - 1, -1, -1):
                    token_count += _estimate_tokens(messages[i])
                    if token_count >= target_tokens:
                        return i
                return 0
            else:
                for i, msg in enumerate(messages):
                    token_count += _estimate_tokens(msg)
                    if token_count >= target_tokens:
                        return i + 1
                return len(messages)
        
        return find_safe_truncation_point(messages, target_tokens, from_end=from_end)
    
    def _log_to_dct(self, event: IntegrityEvent) -> None:
        """Log an event to DCT if configured."""
        if not self.config.dct_enabled or self._dct_logger is None:
            return
        
        try:
            if hasattr(self._dct_logger, "log_event"):
                self._dct_logger.log_event(event)
            elif callable(self._dct_logger):
                self._dct_logger(event)
        except Exception as e:
            logger.error(f"Failed to log to DCT: {e}")


# =============================================================================
# Exception Classes
# =============================================================================

class ContextIntegrityError(Exception):
    """Raised when context integrity validation fails in strict mode."""
    
    def __init__(self, violations: list[IntegrityViolation]):
        self.violations = violations
        violation_summary = "; ".join(
            f"{v['type']} at index {v['message_index']}" 
            for v in violations[:3]
        )
        if len(violations) > 3:
            violation_summary += f" (+{len(violations) - 3} more)"
        super().__init__(f"Context integrity violations: {violation_summary}")


# =============================================================================
# Factory Functions
# =============================================================================

def create_adapter(
    mode: str = "repair",
    dct_logger: DCTLogger | Callable[[IntegrityEvent], None] | None = None,
    **config_kwargs: Any,
) -> ContextIntegrityAdapter:
    """
    Factory function to create a ContextIntegrityAdapter.
    
    Args:
        mode: One of "strict", "repair", or "log-only"
        dct_logger: Optional DCT logger
        **config_kwargs: Additional CIAConfig options
        
    Returns:
        Configured ContextIntegrityAdapter instance
    """
    mode_enum = Mode(mode.lower().replace("_", "-"))
    config = CIAConfig(mode=mode_enum, **config_kwargs)
    return ContextIntegrityAdapter(config=config, dct_logger=dct_logger)
