"""
Substr8 — Governance Infrastructure for AI Agents

Frameworks build agents. Substr8 proves what they did.

Components:
- RunProof: Verification artifacts for every governed run
- FDAA: File-Driven Agent Architecture (agent identity)
- ACC: Agent Capability Control (policy enforcement)
- DCT: Deterministic Computation Trail (audit ledger)
- CIA: Conversation Integrity Assurance (request validation)
- GAM: Git-Native Agent Memory (memory provenance)

Usage:
    # CLI
    substr8 run agent.py
    substr8 verify runproof.tgz
    
    # Python
    from substr8 import govern
    agent = govern(my_agent)
"""

__version__ = "1.8.1"

# High-level API
from .governance import (
    start_run,
    end_run,
    record_action,
    check_policy,
    write_memory,
    search_memory,
)

# Convenience wrapper
def govern(agent, **kwargs):
    """
    Wrap any agent with Substr8 governance.
    
    Auto-detects agent framework and applies appropriate wrapper.
    
    Args:
        agent: Agent instance (CrewAI Crew, DSPy Module, etc.)
        **kwargs: Passed to framework-specific wrapper
        
    Returns:
        Governed agent wrapper
        
    Example:
        from crewai import Crew
        from substr8 import govern
        
        crew = Crew(agents=[...], tasks=[...])
        governed = govern(crew)
        result = governed.kickoff()
    """
    agent_type = type(agent).__name__
    agent_module = type(agent).__module__
    
    # CrewAI detection
    if 'crewai' in agent_module or agent_type == 'Crew':
        from .integrations.crewai import govern_crew
        return govern_crew(agent, **kwargs)
    
    # DSPy detection
    if 'dspy' in agent_module or hasattr(agent, 'forward'):
        from .integrations.dspy import govern_module
        return govern_module(agent, **kwargs)
    
    # LangGraph detection (returns callable wrapper)
    if 'langgraph' in agent_module or hasattr(agent, 'invoke'):
        from .integrations.langgraph import govern_graph
        return govern_graph(agent, **kwargs)
    
    # Generic wrapper for unknown agents
    from .integrations.generic import govern_generic
    return govern_generic(agent, **kwargs)


__all__ = [
    '__version__',
    'govern',
    'start_run',
    'end_run',
    'record_action',
    'check_policy',
    'write_memory',
    'search_memory',
]
