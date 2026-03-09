# Agent identity module
from .manifest import AgentManifest, load_manifest
from .hash import compute_identity_hash, compute_file_hash

__all__ = [
    "AgentManifest",
    "load_manifest", 
    "compute_identity_hash",
    "compute_file_hash",
]
