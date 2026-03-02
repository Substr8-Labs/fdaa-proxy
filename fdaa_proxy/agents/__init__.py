"""
FDAA Agent Registry

Manages agent lifecycle: create, update, delete, spawn.
Agents are defined by persona files (SOUL.md, IDENTITY.md, etc.)
and versioned with cryptographic hashes.
"""

from .models import (
    Agent, AgentVersion, AgentPersona, PersonaFile,
    AgentCreate, AgentUpdate, AgentRollback,
    SpawnRequest, SpawnResult
)
from .registry import AgentRegistry
from .storage import AgentStorage

__all__ = [
    "Agent",
    "AgentVersion",
    "AgentPersona",
    "PersonaFile",
    "AgentCreate",
    "AgentUpdate",
    "AgentRollback",
    "SpawnRequest",
    "SpawnResult",
    "AgentRegistry",
    "AgentStorage",
]
