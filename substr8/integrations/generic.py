"""
Substr8 Generic Integration

Wraps any callable agent with governance tracking.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional


class GovernedAgent:
    """Generic governed wrapper for any callable agent."""
    
    def __init__(
        self,
        agent: Any,
        mcp_url: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.agent = agent
        self.mcp_url = mcp_url or os.environ.get(
            "SUBSTR8_MCP_URL",
            "https://mcp.substr8labs.com"
        )
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:6]}"
        
    def __call__(self, *args, **kwargs) -> Any:
        """Execute the agent with governance enabled."""
        from ..governance import start_run, end_run, record_action
        
        started_at = datetime.now(timezone.utc)
        agent_name = getattr(self.agent, '__name__', type(self.agent).__name__)
        
        start_run(
            run_id=self.run_id,
            agent_ref=f"generic:{agent_name}",
            mcp_url=self.mcp_url,
        )
        
        try:
            if callable(self.agent):
                result = self.agent(*args, **kwargs)
            elif hasattr(self.agent, 'run'):
                result = self.agent.run(*args, **kwargs)
            elif hasattr(self.agent, 'invoke'):
                result = self.agent.invoke(*args, **kwargs)
            else:
                raise TypeError(f"Agent {agent_name} is not callable")
            
            record_action(
                run_id=self.run_id,
                action="agent_completed",
                details={"success": True},
                mcp_url=self.mcp_url,
            )
            
            return result
            
        except Exception as e:
            record_action(
                run_id=self.run_id,
                action="agent_failed",
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
        return getattr(self.agent, name)


def govern_generic(
    agent: Any,
    mcp_url: Optional[str] = None,
    run_id: Optional[str] = None,
) -> GovernedAgent:
    """Wrap any agent with Substr8 governance."""
    return GovernedAgent(agent, mcp_url=mcp_url, run_id=run_id)
