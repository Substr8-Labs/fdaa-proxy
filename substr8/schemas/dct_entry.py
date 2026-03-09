"""
DCT Ledger Entry Schema

Defines the canonical format for audit log entries in the DCT (Delegation Capability Token) ledger.
Each entry is chain-linked (includes prev_hash) for tamper-evident logging.

Key properties:
- Append-only: entries are never modified or deleted
- Chain-linked: each entry includes the hash of the previous entry
- Verifiable: the chain can be recomputed to detect tampering
- Complete: captures agent, version, action, ACC decision, and timestamps

Example entry:
{
  "entry_id": "e-123456",
  "run_id": "run-abc123",
  "seq": 1,
  "timestamp": "2026-03-03T05:00:00.123Z",
  "agent_ref": "substr8/analyst",
  "agent_version": "1.0.0",
  "agent_hash": "sha256:abc123...",
  "action": {
    "type": "tool_call",
    "tool": "web_search",
    "input": {"query": "AI governance"},
    "output": {...}
  },
  "decision": {
    "allowed": true,
    "reason": "tool in capabilities.allow",
    "policy_hash": "sha256:def456..."
  },
  "prev_hash": "sha256:000000...",
  "entry_hash": "sha256:789abc..."
}
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import uuid


class ActionType(str, Enum):
    """Types of actions that can be logged."""
    TOOL_CALL = "tool_call"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    MESSAGE_SEND = "message_send"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    ERROR = "error"


@dataclass
class DCTAction:
    """The action being logged."""
    type: ActionType
    tool: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"type": self.type.value}
        if self.tool:
            result["tool"] = self.tool
        if self.input is not None:
            result["input"] = self.input
        if self.output is not None:
            result["output"] = self.output
        if self.error:
            result["error"] = self.error
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCTAction":
        return cls(
            type=ActionType(data["type"]),
            tool=data.get("tool"),
            input=data.get("input"),
            output=data.get("output"),
            error=data.get("error"),
            duration_ms=data.get("duration_ms"),
        )


@dataclass
class DCTDecision:
    """ACC decision record for the action."""
    allowed: bool
    reason: str
    policy_hash: Optional[str] = None  # Hash of the policy that made the decision
    matched_rule: Optional[str] = None  # Which rule matched (e.g., "capabilities.allow")
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "allowed": self.allowed,
            "reason": self.reason,
        }
        if self.policy_hash:
            result["policy_hash"] = self.policy_hash
        if self.matched_rule:
            result["matched_rule"] = self.matched_rule
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCTDecision":
        return cls(
            allowed=data["allowed"],
            reason=data["reason"],
            policy_hash=data.get("policy_hash"),
            matched_rule=data.get("matched_rule"),
        )
    
    @classmethod
    def allow(cls, reason: str, policy_hash: Optional[str] = None) -> "DCTDecision":
        """Create an allow decision."""
        return cls(allowed=True, reason=reason, policy_hash=policy_hash)
    
    @classmethod
    def deny(cls, reason: str, policy_hash: Optional[str] = None) -> "DCTDecision":
        """Create a deny decision."""
        return cls(allowed=False, reason=reason, policy_hash=policy_hash)


@dataclass
class DCTEntry:
    """
    A single entry in the DCT audit ledger.
    
    Chain-linked: each entry includes prev_hash (hash of previous entry)
    and entry_hash (hash of this entry including prev_hash).
    """
    entry_id: str
    run_id: str
    seq: int  # Sequence number within the run
    timestamp: str  # ISO 8601 with milliseconds
    agent_ref: str
    agent_version: str
    agent_hash: str
    action: DCTAction
    decision: DCTDecision
    prev_hash: str  # Hash of previous entry (genesis = "sha256:0"*64)
    entry_hash: str  # Hash of this entry
    
    # Optional: GAM memory linkage
    memory_entry_hash: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "entry_id": self.entry_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "agent_ref": self.agent_ref,
            "agent_version": self.agent_version,
            "agent_hash": self.agent_hash,
            "action": self.action.to_dict(),
            "decision": self.decision.to_dict(),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }
        if self.memory_entry_hash:
            result["memory_entry_hash"] = self.memory_entry_hash
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCTEntry":
        """Parse from dictionary."""
        return cls(
            entry_id=data["entry_id"],
            run_id=data["run_id"],
            seq=data["seq"],
            timestamp=data["timestamp"],
            agent_ref=data["agent_ref"],
            agent_version=data["agent_version"],
            agent_hash=data["agent_hash"],
            action=DCTAction.from_dict(data["action"]),
            decision=DCTDecision.from_dict(data["decision"]),
            prev_hash=data["prev_hash"],
            entry_hash=data["entry_hash"],
            memory_entry_hash=data.get("memory_entry_hash"),
        )
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=indent)
    
    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON for hashing (no indent, sorted keys)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))
    
    @classmethod
    def compute_entry_hash(
        cls,
        run_id: str,
        seq: int,
        timestamp: str,
        agent_ref: str,
        agent_version: str,
        agent_hash: str,
        action: DCTAction,
        decision: DCTDecision,
        prev_hash: str,
    ) -> str:
        """
        Compute the entry_hash for a new entry.
        
        The hash covers all fields including prev_hash, making it chain-linked.
        """
        # Build canonical representation for hashing
        hash_input = {
            "run_id": run_id,
            "seq": seq,
            "timestamp": timestamp,
            "agent_ref": agent_ref,
            "agent_version": agent_version,
            "agent_hash": agent_hash,
            "action": action.to_dict(),
            "decision": decision.to_dict(),
            "prev_hash": prev_hash,
        }
        
        canonical = json.dumps(hash_input, sort_keys=True, separators=(',', ':'))
        entry_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        
        return f"sha256:{entry_hash}"
    
    @classmethod
    def create(
        cls,
        run_id: str,
        seq: int,
        agent_ref: str,
        agent_version: str,
        agent_hash: str,
        action: DCTAction,
        decision: DCTDecision,
        prev_hash: str,
        memory_entry_hash: Optional[str] = None,
    ) -> "DCTEntry":
        """
        Create a new DCT entry with computed hashes.
        
        Args:
            run_id: Unique identifier for this execution run
            seq: Sequence number within the run (0-indexed)
            agent_ref: Agent reference (namespace/name)
            agent_version: Agent version string
            agent_hash: FDAA agent hash
            action: The action being logged
            decision: ACC decision for the action
            prev_hash: Hash of previous entry (use GENESIS_HASH for first entry)
            memory_entry_hash: Optional GAM memory entry hash
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        entry_id = f"e-{uuid.uuid4().hex[:12]}"
        
        entry_hash = cls.compute_entry_hash(
            run_id=run_id,
            seq=seq,
            timestamp=timestamp,
            agent_ref=agent_ref,
            agent_version=agent_version,
            agent_hash=agent_hash,
            action=action,
            decision=decision,
            prev_hash=prev_hash,
        )
        
        return cls(
            entry_id=entry_id,
            run_id=run_id,
            seq=seq,
            timestamp=timestamp,
            agent_ref=agent_ref,
            agent_version=agent_version,
            agent_hash=agent_hash,
            action=action,
            decision=decision,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            memory_entry_hash=memory_entry_hash,
        )
    
    def verify(self) -> bool:
        """Verify that the entry_hash is correct."""
        computed = self.compute_entry_hash(
            run_id=self.run_id,
            seq=self.seq,
            timestamp=self.timestamp,
            agent_ref=self.agent_ref,
            agent_version=self.agent_version,
            agent_hash=self.agent_hash,
            action=self.action,
            decision=self.decision,
            prev_hash=self.prev_hash,
        )
        return computed == self.entry_hash


# Genesis hash for the first entry in a chain
GENESIS_HASH = "sha256:" + "0" * 64


def verify_chain(entries: List[DCTEntry]) -> List[str]:
    """
    Verify the integrity of a chain of DCT entries.
    
    Returns list of errors (empty if chain is valid).
    """
    errors = []
    
    if not entries:
        return errors
    
    # Sort by sequence number
    sorted_entries = sorted(entries, key=lambda e: e.seq)
    
    for i, entry in enumerate(sorted_entries):
        # Verify entry hash
        if not entry.verify():
            errors.append(f"Entry {entry.entry_id} (seq={entry.seq}): hash mismatch")
        
        # Verify chain link
        if i == 0:
            if entry.prev_hash != GENESIS_HASH:
                errors.append(
                    f"Entry {entry.entry_id} (seq=0): first entry should have genesis prev_hash"
                )
        else:
            expected_prev = sorted_entries[i - 1].entry_hash
            if entry.prev_hash != expected_prev:
                errors.append(
                    f"Entry {entry.entry_id} (seq={entry.seq}): "
                    f"prev_hash mismatch (expected {expected_prev[:20]}...)"
                )
        
        # Verify sequence
        if entry.seq != i:
            errors.append(
                f"Entry {entry.entry_id}: sequence gap (expected {i}, got {entry.seq})"
            )
    
    return errors
