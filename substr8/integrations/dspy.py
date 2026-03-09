"""
Substr8 DSPy Integration

Wraps DSPy modules with governance tracking.

Usage:
    import dspy
    from substr8.integrations import govern_module
    
    class MyAgent(dspy.Module):
        def forward(self, question):
            ...
    
    agent = MyAgent()
    governed = govern_module(agent)
    result = governed(question="What is AI?")
"""

import os
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional

try:
    import dspy
    HAS_DSPY = True
except ImportError:
    HAS_DSPY = False
    dspy = None


class GovernedModule:
    """
    A governed wrapper around DSPy Module.
    
    Records all forward passes and LM calls to DCT ledger.
    """
    
    def __init__(
        self,
        module: Any,
        mcp_url: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        if not HAS_DSPY:
            raise ImportError("dspy not installed. Run: pip install dspy-ai")
        
        self.module = module
        self.mcp_url = mcp_url or os.environ.get(
            "SUBSTR8_MCP_URL",
            "https://mcp.substr8labs.com"
        )
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:6]}"
        self._call_count = 0
        
    def __call__(self, *args, **kwargs) -> Any:
        """Execute the module with governance enabled."""
        from ..governance import start_run, end_run, record_action
        
        started_at = datetime.now(timezone.utc)
        self._call_count += 1
        
        # Start governed run
        start_run(
            run_id=self.run_id,
            agent_ref=f"dspy:{self.module.__class__.__name__}",
            mcp_url=self.mcp_url,
        )
        
        # Record input signature
        record_action(
            run_id=self.run_id,
            action="dspy_forward",
            details={
                "module": self.module.__class__.__name__,
                "call_number": self._call_count,
                "input_keys": list(kwargs.keys()) if kwargs else [f"arg{i}" for i in range(len(args))],
            },
            mcp_url=self.mcp_url,
        )
        
        try:
            # Execute module
            result = self.module(*args, **kwargs)
            
            # Record completion
            record_action(
                run_id=self.run_id,
                action="dspy_completed",
                details={
                    "success": True,
                    "output_type": type(result).__name__,
                },
                mcp_url=self.mcp_url,
            )
            
            return result
            
        except Exception as e:
            record_action(
                run_id=self.run_id,
                action="dspy_failed",
                details={"error": str(e)},
                mcp_url=self.mcp_url,
            )
            raise
            
        finally:
            ended_at = datetime.now(timezone.utc)
            end_run(
                run_id=self.run_id,
                started_at=started_at,
                ended_at=ended_at,
                mcp_url=self.mcp_url,
            )
    
    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to wrapped module."""
        return getattr(self.module, name)


def govern_module(
    module: Any,
    mcp_url: Optional[str] = None,
    run_id: Optional[str] = None,
) -> GovernedModule:
    """
    Wrap a DSPy Module with Substr8 governance.
    
    Args:
        module: A DSPy Module instance
        mcp_url: Governance server URL (default: cloud)
        run_id: Custom run ID (auto-generated if not provided)
        
    Returns:
        GovernedModule wrapper
        
    Example:
        class QA(dspy.Module):
            def __init__(self):
                self.generate = dspy.ChainOfThought("question -> answer")
            
            def forward(self, question):
                return self.generate(question=question)
        
        agent = QA()
        governed = govern_module(agent)
        result = governed(question="What is 2+2?")
    """
    return GovernedModule(module, mcp_url=mcp_url, run_id=run_id)
