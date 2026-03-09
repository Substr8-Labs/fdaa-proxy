"""
ACC - Agent Capability Control

Runtime capability enforcement for agents.
Checks whether actions are allowed based on policy.
"""

__version__ = "0.1.0"

from .check import (
    check,
    check_batch,
    enforce,
    CheckResult,
    load_policy_from_workspace,
    load_policy_from_config,
)

__all__ = [
    "check",
    "check_batch", 
    "enforce",
    "CheckResult",
    "load_policy_from_workspace",
    "load_policy_from_config",
]
