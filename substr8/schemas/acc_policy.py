"""
ACC Policy Schema

Defines the canonical format for Agent Capability Control policies.
ACC enforces what tools/actions an agent is allowed to perform at runtime.

Key properties:
- Declarative: policies are defined as allow/deny rules
- Hashable: policies have a deterministic hash for audit trails
- Composable: multiple policies can be combined (most specific wins)

Example policy:
{
  "policy_id": "pol-abc123",
  "agent_ref": "substr8/analyst",
  "version": "1.0.0",
  "rules": [
    {"action": "allow", "tool": "web_search"},
    {"action": "allow", "tool": "memory_read"},
    {"action": "allow", "tool": "memory_write"},
    {"action": "deny", "tool": "*"}  # Default deny
  ],
  "policy_hash": "sha256:..."
}
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import hashlib
import json


class RuleAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class ACCRule:
    """A single capability rule."""
    action: RuleAction
    tool: str  # Tool name or "*" for wildcard
    conditions: Optional[Dict[str, Any]] = None  # Optional conditions (time bounds, scopes, etc.)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "action": self.action.value,
            "tool": self.tool,
        }
        if self.conditions:
            result["conditions"] = self.conditions
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ACCRule":
        return cls(
            action=RuleAction(data["action"]),
            tool=data["tool"],
            conditions=data.get("conditions"),
        )
    
    @classmethod
    def allow(cls, tool: str, conditions: Optional[Dict[str, Any]] = None) -> "ACCRule":
        """Create an allow rule."""
        return cls(action=RuleAction.ALLOW, tool=tool, conditions=conditions)
    
    @classmethod
    def deny(cls, tool: str, conditions: Optional[Dict[str, Any]] = None) -> "ACCRule":
        """Create a deny rule."""
        return cls(action=RuleAction.DENY, tool=tool, conditions=conditions)
    
    def matches(self, tool_name: str) -> bool:
        """Check if this rule matches the given tool."""
        if self.tool == "*":
            return True
        if self.tool == tool_name:
            return True
        # Support prefix matching with "*" suffix (e.g., "memory_*")
        if self.tool.endswith("*") and tool_name.startswith(self.tool[:-1]):
            return True
        return False


@dataclass
class ACCPolicy:
    """
    Agent Capability Control Policy.
    
    Rules are evaluated in order; first match wins.
    Include a final "deny *" rule for default-deny behavior.
    """
    policy_id: str
    agent_ref: str
    version: str
    rules: List[ACCRule]
    policy_hash: str = ""  # Computed hash of the policy
    
    def __post_init__(self):
        if not self.policy_hash:
            self.policy_hash = self.compute_hash()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "agent_ref": self.agent_ref,
            "version": self.version,
            "rules": [r.to_dict() for r in self.rules],
            "policy_hash": self.policy_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ACCPolicy":
        policy = cls(
            policy_id=data["policy_id"],
            agent_ref=data["agent_ref"],
            version=data["version"],
            rules=[ACCRule.from_dict(r) for r in data.get("rules", [])],
            policy_hash=data.get("policy_hash", ""),
        )
        # Verify or compute hash
        if not policy.policy_hash:
            policy.policy_hash = policy.compute_hash()
        return policy
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=indent)
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of the policy rules."""
        # Hash only the rules (not policy_id which is mutable)
        hash_input = {
            "agent_ref": self.agent_ref,
            "version": self.version,
            "rules": [r.to_dict() for r in self.rules],
        }
        canonical = json.dumps(hash_input, sort_keys=True, separators=(',', ':'))
        policy_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        return f"sha256:{policy_hash}"
    
    def check(self, tool_name: str) -> tuple[bool, str, Optional[ACCRule]]:
        """
        Check if a tool is allowed by this policy.
        
        Returns:
            (allowed: bool, reason: str, matched_rule: Optional[ACCRule])
        """
        for rule in self.rules:
            if rule.matches(tool_name):
                if rule.action == RuleAction.ALLOW:
                    return (True, f"Allowed by rule: {rule.tool}", rule)
                else:
                    return (False, f"Denied by rule: {rule.tool}", rule)
        
        # No matching rule - default allow (explicit deny rules should be added)
        return (True, "No matching rule (default allow)", None)
    
    @classmethod
    def from_agent_spec(cls, agent_ref: str, version: str, capabilities: Dict[str, Any]) -> "ACCPolicy":
        """
        Create an ACC policy from an AgentSpec capabilities block.
        
        Converts:
            capabilities:
              allow: [web_search, memory_read]
              deny: [shell_exec]
        
        To ordered rules with default deny.
        """
        import uuid
        
        rules = []
        
        # Add allow rules first
        for tool in capabilities.get("allow", []):
            rules.append(ACCRule.allow(tool))
        
        # Add deny rules
        for tool in capabilities.get("deny", []):
            rules.append(ACCRule.deny(tool))
        
        # Add default deny if we have any rules
        if rules:
            rules.append(ACCRule.deny("*"))
        
        return cls(
            policy_id=f"pol-{uuid.uuid4().hex[:12]}",
            agent_ref=agent_ref,
            version=version,
            rules=rules,
        )
    
    def validate(self) -> List[str]:
        """Validate the policy and return list of errors."""
        errors = []
        
        if not self.rules:
            errors.append("Policy has no rules")
        
        # Check for duplicate exact rules
        seen = set()
        for rule in self.rules:
            key = (rule.action.value, rule.tool)
            if key in seen:
                errors.append(f"Duplicate rule: {rule.action.value} {rule.tool}")
            seen.add(key)
        
        # Check that wildcard deny is last (if present)
        wildcard_indices = [i for i, r in enumerate(self.rules) 
                          if r.tool == "*" and r.action == RuleAction.DENY]
        if wildcard_indices and wildcard_indices[0] != len(self.rules) - 1:
            errors.append("Wildcard deny rule should be last (rules after it are unreachable)")
        
        # Verify hash
        computed = self.compute_hash()
        if self.policy_hash and self.policy_hash != computed:
            errors.append(f"Policy hash mismatch: stored {self.policy_hash}, computed {computed}")
        
        return errors
