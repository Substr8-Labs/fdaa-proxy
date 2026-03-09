"""Agent identity hashing."""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from .manifest import AgentManifest, load_manifest


@dataclass
class FileHash:
    """Hash of a single file."""
    path: str
    hash: str
    size: int


@dataclass
class IdentityHash:
    """Complete identity hash result."""
    agent_name: str
    agent_version: str
    identity_hash: str
    manifest_hash: str
    files: List[FileHash]
    
    def to_dict(self) -> Dict:
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "identity_hash": self.identity_hash,
            "manifest_hash": self.manifest_hash,
            "files": [
                {"path": f.path, "hash": f.hash, "size": f.size}
                for f in self.files
            ]
        }


def compute_file_hash(path: Path) -> Tuple[str, int]:
    """Compute SHA256 hash of a file.
    
    Returns:
        Tuple of (hash_string, file_size)
    """
    hasher = hashlib.sha256()
    size = 0
    
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
            size += len(chunk)
    
    return f"sha256:{hasher.hexdigest()}", size


def normalize_manifest(manifest: AgentManifest) -> str:
    """Create normalized string representation for hashing.
    
    This ensures consistent hashing regardless of:
    - Key ordering in YAML
    - Whitespace differences
    - Optional field presence
    """
    # Build normalized dict with sorted keys, excluding None values
    normalized = {
        "name": manifest.name,
        "version": manifest.version,
    }
    
    # Add identity (only non-None values)
    identity = {}
    if manifest.identity.persona:
        identity["persona"] = manifest.identity.persona
    if manifest.identity.capabilities:
        identity["capabilities"] = manifest.identity.capabilities
    if manifest.identity.memory_policy:
        identity["memory_policy"] = manifest.identity.memory_policy
    if identity:
        normalized["identity"] = identity
    
    # Add runtime
    runtime = {"framework": manifest.runtime.framework}
    if manifest.runtime.entry:
        runtime["entry"] = manifest.runtime.entry
    normalized["runtime"] = runtime
    
    # Add governance (sorted lists)
    governance = {}
    if manifest.governance.allowed_tools:
        governance["allowed_tools"] = sorted(manifest.governance.allowed_tools)
    if manifest.governance.denied_tools:
        governance["denied_tools"] = sorted(manifest.governance.denied_tools)
    if governance:
        normalized["governance"] = governance
    
    # Convert to JSON with sorted keys
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def compute_identity_hash(
    path: Path,
    include_files: bool = True
) -> IdentityHash:
    """Compute complete identity hash for an agent.
    
    The identity hash is computed from:
    1. Normalized manifest content
    2. Hashes of all referenced identity files
    
    Args:
        path: Path to agent directory or manifest file
        include_files: Whether to include file content hashes
        
    Returns:
        IdentityHash with all computed values
    """
    # Load manifest
    manifest = load_manifest(path)
    
    # Compute manifest hash (structure only)
    normalized = normalize_manifest(manifest)
    manifest_hash = hashlib.sha256(normalized.encode()).hexdigest()
    manifest_hash = f"sha256:{manifest_hash}"
    
    # Compute file hashes
    file_hashes: List[FileHash] = []
    
    if include_files:
        for file_path in manifest.get_all_files():
            if file_path.exists():
                hash_str, size = compute_file_hash(file_path)
                rel_path = str(file_path.relative_to(manifest.base_path)) if manifest.base_path else str(file_path)
                file_hashes.append(FileHash(
                    path=rel_path,
                    hash=hash_str,
                    size=size
                ))
    
    # Compute identity hash (manifest + all file hashes)
    identity_parts = [manifest_hash]
    for fh in sorted(file_hashes, key=lambda x: x.path):
        identity_parts.append(f"{fh.path}:{fh.hash}")
    
    identity_string = "\n".join(identity_parts)
    identity_hash = hashlib.sha256(identity_string.encode()).hexdigest()
    identity_hash = f"sha256:{identity_hash}"
    
    return IdentityHash(
        agent_name=manifest.name,
        agent_version=manifest.version,
        identity_hash=identity_hash,
        manifest_hash=manifest_hash,
        files=file_hashes
    )


def verify_identity(
    path: Path,
    expected_hash: str
) -> Tuple[bool, Optional[str]]:
    """Verify an agent matches an expected identity hash.
    
    Args:
        path: Path to agent
        expected_hash: Expected identity hash
        
    Returns:
        Tuple of (matches, actual_hash)
    """
    result = compute_identity_hash(path)
    matches = result.identity_hash == expected_hash
    return matches, result.identity_hash
