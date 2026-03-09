"""
Substr8 LangGraph Integration

Wraps LangGraph graphs with governance tracking.

Usage:
    from langgraph.prebuilt import create_react_agent
    from substr8.integrations import govern_graph
    
    graph = create_react_agent(llm, tools)
    governed = govern_graph(graph)
    result = governed.invoke({"messages": [...]})
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


class GovernedGraph:
    """
    A governed wrapper around LangGraph graphs.
    """
    
    def __init__(
        self,
        graph: Any,
        mcp_url: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.graph = graph
        self.mcp_url = mcp_url or os.environ.get(
            "SUBSTR8_MCP_URL",
            "https://mcp.substr8labs.com"
        )
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:6]}"
        
    def invoke(self, inputs: Any, **kwargs) -> Any:
        """Execute the graph with governance enabled."""
        from ..governance import start_run, end_run, record_action
        
        started_at = datetime.now(timezone.utc)
        
        start_run(
            run_id=self.run_id,
            agent_ref="langgraph:agent",
            mcp_url=self.mcp_url,
        )
        
        try:
            result = self.graph.invoke(inputs, **kwargs)
            
            record_action(
                run_id=self.run_id,
                action="graph_completed",
                details={"success": True},
                mcp_url=self.mcp_url,
            )
            
            return result
            
        except Exception as e:
            record_action(
                run_id=self.run_id,
                action="graph_failed",
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
    
    def stream(self, inputs: Any, **kwargs):
        """Stream execution with governance."""
        from ..governance import start_run, end_run, record_action
        
        started_at = datetime.now(timezone.utc)
        
        start_run(
            run_id=self.run_id,
            agent_ref="langgraph:agent",
            mcp_url=self.mcp_url,
        )
        
        try:
            for chunk in self.graph.stream(inputs, **kwargs):
                yield chunk
                
            record_action(
                run_id=self.run_id,
                action="graph_stream_completed",
                details={"success": True},
                mcp_url=self.mcp_url,
            )
            
        except Exception as e:
            record_action(
                run_id=self.run_id,
                action="graph_stream_failed",
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
        return getattr(self.graph, name)


def govern_graph(
    graph: Any,
    mcp_url: Optional[str] = None,
    run_id: Optional[str] = None,
) -> GovernedGraph:
    """Wrap a LangGraph graph with Substr8 governance."""
    return GovernedGraph(graph, mcp_url=mcp_url, run_id=run_id)
