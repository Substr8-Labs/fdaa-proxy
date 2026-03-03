"""
FDAA Governance Layer

Integrates substr8 platform primitives into the proxy:
- ACC (Agent Capability Control) - runtime enforcement
- DCT (Audit Ledger) - tamper-evident logging
- CIA (Context Integrity Adapter) - structural validation

This module bridges the proxy with the canonical substr8 schemas.
"""

import os
import sys
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from pathlib import Path

# Add substr8-cli to path for imports
SUBSTR8_CLI_PATH = Path(__file__).parent.parent.parent / "substr8-cli"
if str(SUBSTR8_CLI_PATH) not in sys.path:
    sys.path.insert(0, str(SUBSTR8_CLI_PATH))

from substr8.schemas import (
    DCTEntry,
    DCTAction,
    DCTDecision,
    ActionType,
    GENESIS_HASH,
    ACCPolicy,
)
from substr8.dct.ledger import DCTLedger
from substr8.acc import check as acc_check, CheckResult

logger = logging.getLogger("fdaa-governance")


@dataclass
class GovernanceContext:
    """Context for a governed execution."""
    run_id: str
    agent_ref: str
    agent_version: str
    agent_hash: str
    session_id: Optional[str] = None


class GovernanceLayer:
    """
    Unified governance layer for FDAA Proxy.
    
    Provides:
    - ACC capability checks before tool execution
    - DCT audit logging for all actions
    - Integration with substr8 platform schemas
    """
    
    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        acc_enabled: bool = True,
        dct_enabled: bool = True,
    ):
        self.acc_enabled = acc_enabled
        self.dct_enabled = dct_enabled
        
        # Initialize DCT ledger
        if dct_enabled:
            self.ledger = DCTLedger(path=ledger_path)
        else:
            self.ledger = None
        
        # Cache for loaded ACC policies
        self._policy_cache: Dict[str, ACCPolicy] = {}
    
    def load_policy(self, agent_id: str, workspace: Optional[Path] = None) -> Optional[ACCPolicy]:
        """Load ACC policy for an agent."""
        if agent_id in self._policy_cache:
            return self._policy_cache[agent_id]
        
        # Try to load from workspace
        if workspace:
            policy_path = workspace / ".fdaa" / "policy.json"
            if policy_path.exists():
                import json
                with open(policy_path) as f:
                    policy = ACCPolicy.from_dict(json.load(f))
                    self._policy_cache[agent_id] = policy
                    return policy
        
        # Try to load from OpenClaw config
        from substr8.acc import load_policy_from_config
        policy = load_policy_from_config(agent_id)
        if policy:
            self._policy_cache[agent_id] = policy
        
        return policy
    
    def check_capability(
        self,
        ctx: GovernanceContext,
        tool: str,
    ) -> CheckResult:
        """
        Check if an agent can use a tool.
        
        Returns CheckResult with allow/deny decision.
        """
        if not self.acc_enabled:
            return CheckResult(
                allowed=True,
                reason="ACC disabled",
                tool=tool,
                agent_ref=ctx.agent_ref,
                policy_hash="",
            )
        
        return acc_check(ctx.agent_ref.split("/")[-1], tool)
    
    def log_tool_call(
        self,
        ctx: GovernanceContext,
        tool: str,
        input_args: Dict[str, Any],
        output: Optional[Dict[str, Any]],
        decision: DCTDecision,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Optional[DCTEntry]:
        """
        Log a tool call to the DCT audit ledger.
        
        Returns the created DCTEntry, or None if DCT is disabled.
        """
        if not self.dct_enabled or not self.ledger:
            return None
        
        action = DCTAction(
            type=ActionType.TOOL_CALL,
            tool=tool,
            input=input_args,
            output=output,
            error=error,
            duration_ms=duration_ms,
        )
        
        entry = self.ledger.append(
            run_id=ctx.run_id,
            agent_ref=ctx.agent_ref,
            agent_version=ctx.agent_version,
            agent_hash=ctx.agent_hash,
            action=action,
            decision=decision,
        )
        
        logger.info(
            f"DCT logged: {entry.entry_id} | "
            f"{tool} | "
            f"{'ALLOWED' if decision.allowed else 'DENIED'}"
        )
        
        return entry
    
    def log_memory_op(
        self,
        ctx: GovernanceContext,
        operation: str,  # "read" or "write"
        key: str,
        decision: DCTDecision,
        memory_entry_hash: Optional[str] = None,
    ) -> Optional[DCTEntry]:
        """Log a memory operation to DCT."""
        if not self.dct_enabled or not self.ledger:
            return None
        
        action_type = ActionType.MEMORY_READ if operation == "read" else ActionType.MEMORY_WRITE
        
        action = DCTAction(
            type=action_type,
            input={"key": key, "operation": operation},
        )
        
        entry = self.ledger.append(
            run_id=ctx.run_id,
            agent_ref=ctx.agent_ref,
            agent_version=ctx.agent_version,
            agent_hash=ctx.agent_hash,
            action=action,
            decision=decision,
            memory_entry_hash=memory_entry_hash,
        )
        
        return entry
    
    def log_agent_start(self, ctx: GovernanceContext) -> Optional[DCTEntry]:
        """Log agent session start."""
        if not self.dct_enabled or not self.ledger:
            return None
        
        action = DCTAction(
            type=ActionType.AGENT_START,
            input={"session_id": ctx.session_id},
        )
        
        decision = DCTDecision.allow("Session started")
        
        return self.ledger.append(
            run_id=ctx.run_id,
            agent_ref=ctx.agent_ref,
            agent_version=ctx.agent_version,
            agent_hash=ctx.agent_hash,
            action=action,
            decision=decision,
        )
    
    def log_agent_end(
        self,
        ctx: GovernanceContext,
        reason: str = "Session ended",
    ) -> Optional[DCTEntry]:
        """Log agent session end."""
        if not self.dct_enabled or not self.ledger:
            return None
        
        action = DCTAction(
            type=ActionType.AGENT_END,
            input={"reason": reason},
        )
        
        decision = DCTDecision.allow(reason)
        
        return self.ledger.append(
            run_id=ctx.run_id,
            agent_ref=ctx.agent_ref,
            agent_version=ctx.agent_version,
            agent_hash=ctx.agent_hash,
            action=action,
            decision=decision,
        )
    
    def verify_run(self, run_id: str) -> Dict[str, Any]:
        """Verify the chain integrity of a run."""
        if not self.ledger:
            return {"verified": False, "error": "DCT disabled"}
        
        return self.ledger.verify_run(run_id)
    
    def export_run(self, run_id: str) -> Dict[str, Any]:
        """Export a run for audit."""
        if not self.ledger:
            return {"error": "DCT disabled"}
        
        return self.ledger.export_run(run_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get governance statistics."""
        stats = {
            "acc_enabled": self.acc_enabled,
            "dct_enabled": self.dct_enabled,
            "policies_cached": len(self._policy_cache),
        }
        
        if self.ledger:
            stats["ledger"] = self.ledger.stats()
        
        return stats


# Middleware for tool call governance
async def govern_tool_call(
    governance: GovernanceLayer,
    ctx: GovernanceContext,
    tool: str,
    arguments: Dict[str, Any],
    execute_fn,
) -> Dict[str, Any]:
    """
    Governs a tool call with ACC check and DCT logging.
    
    Args:
        governance: GovernanceLayer instance
        ctx: Execution context
        tool: Tool name
        arguments: Tool arguments
        execute_fn: Async function to execute the tool (called if allowed)
    
    Returns:
        Result dict with status, result/error, and audit info
    """
    import time
    
    # 1. ACC check
    check_result = governance.check_capability(ctx, tool)
    decision = check_result.to_dct_decision()
    
    if not check_result.allowed:
        # Log denied attempt
        governance.log_tool_call(
            ctx=ctx,
            tool=tool,
            input_args=arguments,
            output=None,
            decision=decision,
            error=f"ACC denied: {check_result.reason}",
        )
        
        return {
            "status": "denied",
            "reason": check_result.reason,
            "policy_hash": check_result.policy_hash,
            "audit_logged": True,
        }
    
    # 2. Execute tool
    start_time = time.time()
    try:
        result = await execute_fn(tool, arguments)
        duration_ms = int((time.time() - start_time) * 1000)
        
        # 3. Log success
        entry = governance.log_tool_call(
            ctx=ctx,
            tool=tool,
            input_args=arguments,
            output={"result": str(result)[:1000]},  # Truncate large outputs
            decision=decision,
            duration_ms=duration_ms,
        )
        
        return {
            "status": "success",
            "result": result,
            "duration_ms": duration_ms,
            "entry_id": entry.entry_id if entry else None,
        }
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Log error
        governance.log_tool_call(
            ctx=ctx,
            tool=tool,
            input_args=arguments,
            output=None,
            decision=decision,
            duration_ms=duration_ms,
            error=str(e),
        )
        
        return {
            "status": "error",
            "error": str(e),
            "duration_ms": duration_ms,
            "audit_logged": True,
        }


# Factory function
def create_governance_layer(
    ledger_path: Optional[str] = None,
    acc_enabled: bool = True,
    dct_enabled: bool = True,
) -> GovernanceLayer:
    """Create a governance layer with configuration."""
    path = Path(ledger_path) if ledger_path else None
    return GovernanceLayer(
        ledger_path=path,
        acc_enabled=acc_enabled,
        dct_enabled=dct_enabled,
    )
