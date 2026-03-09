"""
Substr8 Platform Schemas

Canonical data contracts for the platform primitives:
- tower/v1: Agent definition spec
- manifest: FDAA artifact manifest with deterministic hashing
- dct_entry: DCT ledger entry (chain-linked audit log)
- acc_policy: ACC capability policy
"""

from .tower_v1 import AgentSpec, AgentMetadata, AgentCapabilities, AgentMemoryConfig
from .manifest import Manifest, FileHash, ManifestMeta
from .dct_entry import DCTEntry, DCTAction, DCTDecision, GENESIS_HASH, verify_chain, ActionType
from .acc_policy import ACCPolicy, ACCRule

__all__ = [
    # Tower v1
    "AgentSpec",
    "AgentMetadata", 
    "AgentCapabilities",
    "AgentMemoryConfig",
    # Manifest
    "Manifest",
    "FileHash",
    "ManifestMeta",
    # DCT
    "DCTEntry",
    "DCTAction",
    "DCTDecision",
    "ActionType",
    "GENESIS_HASH",
    "verify_chain",
    # ACC
    "ACCPolicy",
    "ACCRule",
]
