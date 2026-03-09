"""
Substr8 CrewAI Integration

Wraps CrewAI crews with governance tracking.

Usage:
    from crewai import Crew, Agent, Task
    from substr8.integrations import govern_crew
    
    crew = Crew(agents=[...], tasks=[...])
    governed = govern_crew(crew)
    result = governed.kickoff()
"""

import os
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional

try:
    from crewai import Crew
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False
    Crew = None


class GovernedCrew:
    """
    A governed wrapper around CrewAI Crew.
    
    Records all task executions and agent interactions to DCT ledger.
    """
    
    def __init__(
        self,
        crew: Any,
        mcp_url: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        if not HAS_CREWAI:
            raise ImportError("crewai not installed. Run: pip install crewai")
        
        self.crew = crew
        self.mcp_url = mcp_url or os.environ.get(
            "SUBSTR8_MCP_URL", 
            "https://mcp.substr8labs.com"
        )
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:6]}"
        self._ledger = []
        
    def kickoff(self, inputs: Optional[dict] = None) -> Any:
        """Execute the crew with governance enabled."""
        from ..governance import start_run, end_run, record_action
        
        started_at = datetime.now(timezone.utc)
        
        # Start governed run
        start_run(
            run_id=self.run_id,
            agent_ref=f"crewai:{self.crew.name if hasattr(self.crew, 'name') else 'crew'}",
            mcp_url=self.mcp_url,
        )
        
        # Record crew composition
        for i, agent in enumerate(self.crew.agents):
            record_action(
                run_id=self.run_id,
                action="agent_registered",
                details={
                    "index": i,
                    "role": getattr(agent, 'role', 'unknown'),
                    "goal": getattr(agent, 'goal', None),
                },
                mcp_url=self.mcp_url,
            )
        
        try:
            # Execute crew
            result = self.crew.kickoff(inputs=inputs)
            
            # Record completion
            record_action(
                run_id=self.run_id,
                action="crew_completed",
                details={"success": True},
                mcp_url=self.mcp_url,
            )
            
            return result
            
        except Exception as e:
            record_action(
                run_id=self.run_id,
                action="crew_failed",
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


def govern_crew(
    crew: Any,
    mcp_url: Optional[str] = None,
    run_id: Optional[str] = None,
) -> GovernedCrew:
    """
    Wrap a CrewAI Crew with Substr8 governance.
    
    Args:
        crew: A CrewAI Crew instance
        mcp_url: Governance server URL (default: cloud)
        run_id: Custom run ID (auto-generated if not provided)
        
    Returns:
        GovernedCrew wrapper
        
    Example:
        crew = Crew(agents=[...], tasks=[...])
        governed = govern_crew(crew)
        result = governed.kickoff()
    """
    return GovernedCrew(crew, mcp_url=mcp_url, run_id=run_id)
