"""
Substr8 Governance Helpers

Low-level functions for interacting with the governance plane.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import urllib.request
import urllib.error


def _mcp_call(mcp_url: str, tool: str, params: Dict[str, Any]) -> Optional[Dict]:
    """Make a call to the MCP server."""
    url = f"{mcp_url}/tools/{tool}"
    data = json.dumps(params).encode('utf-8')
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.URLError as e:
        # MCP not available, log locally
        _local_log(tool, params)
        return None
    except Exception as e:
        _local_log(tool, params, error=str(e))
        return None


def _local_log(tool: str, params: Dict[str, Any], error: Optional[str] = None):
    """Log to local file when MCP unavailable."""
    log_dir = os.environ.get("SUBSTR8_LOG_DIR", ".substr8")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "governance.jsonl")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "params": params,
    }
    if error:
        entry["error"] = error
        
    with open(log_file, 'a') as f:
        f.write(json.dumps(entry) + "\n")


def start_run(
    run_id: str,
    agent_ref: str,
    mcp_url: str,
    policy_hash: Optional[str] = None,
) -> Optional[Dict]:
    """Start a governed run."""
    return _mcp_call(mcp_url, "run.start", {
        "run_id": run_id,
        "agent_ref": agent_ref,
        "policy_hash": policy_hash or "default",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def end_run(
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    mcp_url: str,
    success: bool = True,
) -> Optional[Dict]:
    """End a governed run and finalize RunProof."""
    return _mcp_call(mcp_url, "run.end", {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "success": success,
    })


def record_action(
    run_id: str,
    action: str,
    mcp_url: str,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[Dict]:
    """Record an action to the audit ledger."""
    return _mcp_call(mcp_url, "tool.invoke", {
        "run_id": run_id,
        "tool_name": action,
        "input": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def check_policy(
    run_id: str,
    action: str,
    mcp_url: str,
    resource: Optional[str] = None,
) -> bool:
    """Check if an action is allowed by policy."""
    result = _mcp_call(mcp_url, "policy.check", {
        "run_id": run_id,
        "action": action,
        "resource": resource,
    })
    
    if result is None:
        # Default allow if MCP unavailable
        return True
    
    return result.get("allowed", True)


def write_memory(
    agent_id: str,
    content: str,
    mcp_url: str,
    run_id: Optional[str] = None,
    memory_type: str = "observation",
) -> Optional[Dict]:
    """Write to governed memory."""
    return _mcp_call(mcp_url, "memory.write", {
        "agent_id": agent_id,
        "content": content,
        "memory_type": memory_type,
        "run_id": run_id,
    })


def search_memory(
    agent_id: str,
    query: str,
    mcp_url: str,
    limit: int = 5,
) -> Optional[list]:
    """Search governed memory."""
    result = _mcp_call(mcp_url, "memory.search", {
        "agent_id": agent_id,
        "query": query,
        "limit": limit,
    })
    
    return result.get("results", []) if result else []
