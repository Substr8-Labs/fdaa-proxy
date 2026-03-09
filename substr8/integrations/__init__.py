"""
Substr8 Framework Integrations

Wrappers for popular agent frameworks to enable governance.
"""

from .crewai import govern_crew, GovernedCrew
from .dspy import govern_module, GovernedModule
from .langgraph import govern_graph, GovernedGraph
from .generic import govern_generic, GovernedAgent

__all__ = [
    'govern_crew',
    'GovernedCrew',
    'govern_module', 
    'GovernedModule',
    'govern_graph',
    'GovernedGraph',
    'govern_generic',
    'GovernedAgent',
]
