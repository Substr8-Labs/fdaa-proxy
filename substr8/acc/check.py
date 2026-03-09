"""
ACC Check - Runtime capability enforcement

Provides runtime checks for whether an agent can perform an action.
Used by fdaa-proxy to enforce capabilities.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from ..schemas import ACCPolicy, ACCRule, DCTDecision


@dataclass
class CheckResult:
    """Result of an ACC capability check."""
    allowed: bool
    reason: str
    tool: str
    agent_ref: str
    policy_hash: str
    matched_rule: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "tool": self.tool,
            "agent_ref": self.agent_ref,
            "policy_hash": self.policy_hash,
            "matched_rule": self.matched_rule,
        }
    
    def to_dct_decision(self) -> DCTDecision:
        """Convert to DCT decision record for audit logging."""
        return DCTDecision(
            allowed=self.allowed,
            reason=self.reason,
            policy_hash=self.policy_hash,
            matched_rule=self.matched_rule,
        )


def load_policy_from_workspace(workspace: Path) -> Optional[ACCPolicy]:
    """Load ACC policy from a provisioned agent workspace."""
    policy_path = workspace / ".fdaa" / "policy.json"
    
    if not policy_path.exists():
        return None
    
    with open(policy_path) as f:
        return ACCPolicy.from_dict(json.load(f))


def load_policy_from_config(agent_id: str) -> Optional[ACCPolicy]:
    """Load ACC policy for an agent from OpenClaw config."""
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    
    if not config_path.exists():
        return None
    
    with open(config_path) as f:
        config = json.load(f)
    
    # Find agent in config
    for agent in config.get("agents", {}).get("list", []):
        if agent.get("id") == agent_id:
            workspace = Path(agent.get("workspace", ""))
            if workspace.exists():
                return load_policy_from_workspace(workspace)
    
    return None


def check(
    agent_id: str,
    tool: str,
    policy: Optional[ACCPolicy] = None,
) -> CheckResult:
    """
    Check if an agent is allowed to use a tool.
    
    Args:
        agent_id: Agent identifier
        tool: Tool name to check
        policy: Optional pre-loaded policy (loads from workspace if not provided)
    
    Returns:
        CheckResult with allow/deny decision
    """
    # Load policy if not provided
    if policy is None:
        policy = load_policy_from_config(agent_id)
    
    if policy is None:
        # No policy = allow (but flag as unverified)
        return CheckResult(
            allowed=True,
            reason="No ACC policy found (unverified)",
            tool=tool,
            agent_ref=agent_id,
            policy_hash="",
            matched_rule=None,
        )
    
    # Run the check
    allowed, reason, matched_rule = policy.check(tool)
    
    return CheckResult(
        allowed=allowed,
        reason=reason,
        tool=tool,
        agent_ref=policy.agent_ref,
        policy_hash=policy.policy_hash,
        matched_rule=matched_rule.tool if matched_rule else None,
    )


def check_batch(
    agent_id: str,
    tools: list[str],
    policy: Optional[ACCPolicy] = None,
) -> Dict[str, CheckResult]:
    """
    Check multiple tools at once.
    
    Returns:
        Dict mapping tool name to CheckResult
    """
    # Load policy once
    if policy is None:
        policy = load_policy_from_config(agent_id)
    
    return {
        tool: check(agent_id, tool, policy)
        for tool in tools
    }


def enforce(
    agent_id: str,
    tool: str,
    policy: Optional[ACCPolicy] = None,
) -> CheckResult:
    """
    Enforce capability check - raises if denied.
    
    Use this in the proxy to gate tool execution.
    
    Raises:
        PermissionError: If the tool is denied
    """
    result = check(agent_id, tool, policy)
    
    if not result.allowed:
        raise PermissionError(
            f"ACC denied: {result.reason} "
            f"(agent={agent_id}, tool={tool}, policy={result.policy_hash[:20]}...)"
        )
    
    return result
