"""
FDAA Manifest Schema

Defines the canonical manifest format for agent artifacts.
The manifest provides deterministic hashing of the complete agent bundle.

Key properties:
- Deterministic: same files → same hash (sorted paths, canonical JSON)
- Immutable: once published, the manifest is frozen
- Verifiable: any party can recompute and verify the agent_hash

Example manifest:
{
  "schema_version": "1.0",
  "agent_ref": "substr8/analyst",
  "version": "1.0.0",
  "agent_hash": "sha256:abc123...",
  "files": [
    {"path": "agent.yaml", "hash": "sha256:def456...", "size": 512},
    {"path": "SOUL.md", "hash": "sha256:789abc...", "size": 1024},
    ...
  ],
  "created_at": "2026-03-03T05:00:00Z",
  "created_by": "ada@substr8labs.com"
}
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import hashlib
import json
import os


SCHEMA_VERSION = "1.0"


@dataclass
class FileHash:
    """Hash entry for a single file in the manifest."""
    path: str
    hash: str  # "sha256:..."
    size: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "hash": self.hash,
            "size": self.size,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileHash":
        return cls(
            path=data["path"],
            hash=data["hash"],
            size=data["size"],
        )
    
    @classmethod
    def from_file(cls, base_path: str, relative_path: str) -> "FileHash":
        """Compute hash for a file."""
        full_path = os.path.join(base_path, relative_path)
        
        with open(full_path, 'rb') as f:
            content = f.read()
        
        file_hash = hashlib.sha256(content).hexdigest()
        
        return cls(
            path=relative_path,
            hash=f"sha256:{file_hash}",
            size=len(content),
        )


@dataclass
class ManifestMeta:
    """Metadata about manifest creation."""
    created_at: str  # ISO 8601
    created_by: Optional[str] = None
    signature: Optional[str] = None  # Ed25519 signature if signed
    signed_by: Optional[str] = None  # Key ID if signed
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"created_at": self.created_at}
        if self.created_by:
            result["created_by"] = self.created_by
        if self.signature:
            result["signature"] = self.signature
        if self.signed_by:
            result["signed_by"] = self.signed_by
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ManifestMeta":
        return cls(
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            created_by=data.get("created_by"),
            signature=data.get("signature"),
            signed_by=data.get("signed_by"),
        )


@dataclass
class Manifest:
    """
    FDAA Agent Manifest.
    
    Contains all file hashes and the computed agent_hash for registry publication.
    The agent_hash is computed deterministically from the sorted file list.
    """
    schema_version: str
    agent_ref: str  # "namespace/name"
    version: str
    agent_hash: str  # "sha256:..." - computed from files
    files: List[FileHash]
    meta: ManifestMeta = field(default_factory=lambda: ManifestMeta(
        created_at=datetime.now(timezone.utc).isoformat()
    ))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "schema_version": self.schema_version,
            "agent_ref": self.agent_ref,
            "version": self.version,
            "agent_hash": self.agent_hash,
            "files": [f.to_dict() for f in self.files],
            **self.meta.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        """Parse from dictionary."""
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            agent_ref=data["agent_ref"],
            version=data["version"],
            agent_hash=data["agent_hash"],
            files=[FileHash.from_dict(f) for f in data.get("files", [])],
            meta=ManifestMeta.from_dict(data),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "Manifest":
        """Parse from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to canonical JSON (sorted keys for determinism)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)
    
    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON (no indent, sorted keys) for hashing."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))
    
    @classmethod
    def compute_agent_hash(cls, files: List[FileHash]) -> str:
        """
        Compute the agent_hash from file hashes.
        
        Algorithm:
        1. Sort files by path (deterministic ordering)
        2. Concatenate "path:hash" for each file
        3. SHA256 the result
        
        This ensures the same files always produce the same agent_hash.
        """
        # Sort by path for deterministic ordering
        sorted_files = sorted(files, key=lambda f: f.path)
        
        # Build hash input: "path1:hash1\npath2:hash2\n..."
        hash_input = "\n".join(f"{f.path}:{f.hash}" for f in sorted_files)
        
        # Compute SHA256
        agent_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
        
        return f"sha256:{agent_hash}"
    
    @classmethod
    def from_directory(
        cls,
        path: str,
        agent_ref: str,
        version: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        created_by: Optional[str] = None,
    ) -> "Manifest":
        """
        Generate manifest from a directory of agent files.
        
        Args:
            path: Base directory containing agent files
            agent_ref: Agent reference (namespace/name)
            version: Version string
            include_patterns: File patterns to include (default: common agent files)
            exclude_patterns: File patterns to exclude (default: hidden files, __pycache__)
            created_by: Author identifier
        """
        if include_patterns is None:
            include_patterns = [
                "agent.yaml",
                "*.md",
                "tools/*.yaml",
                "skills/*.yaml",
            ]
        
        if exclude_patterns is None:
            exclude_patterns = [
                ".*",
                "__pycache__",
                "*.pyc",
                "node_modules",
            ]
        
        # Collect all matching files
        files = []
        for root, dirs, filenames in os.walk(path):
            # Filter directories
            dirs[:] = [d for d in dirs if not any(
                d.startswith(p.rstrip('*')) for p in exclude_patterns if '*' not in p or p.startswith('.')
            )]
            
            for filename in filenames:
                # Skip excluded patterns
                if any(filename.startswith('.') for p in exclude_patterns if p == '.*'):
                    continue
                if filename.endswith('.pyc'):
                    continue
                
                # Check if file matches include patterns
                rel_path = os.path.relpath(os.path.join(root, filename), path)
                
                # Simple pattern matching
                should_include = False
                for pattern in include_patterns:
                    if pattern.startswith('*.'):
                        if filename.endswith(pattern[1:]):
                            should_include = True
                            break
                    elif '/' in pattern:
                        # Directory pattern like "tools/*.yaml"
                        dir_part, file_part = pattern.rsplit('/', 1)
                        if rel_path.startswith(dir_part + '/'):
                            if file_part.startswith('*.'):
                                if filename.endswith(file_part[1:]):
                                    should_include = True
                                    break
                    elif filename == pattern or rel_path == pattern:
                        should_include = True
                        break
                
                if should_include:
                    file_hash = FileHash.from_file(path, rel_path)
                    files.append(file_hash)
        
        # Compute agent hash
        agent_hash = cls.compute_agent_hash(files)
        
        return cls(
            schema_version=SCHEMA_VERSION,
            agent_ref=agent_ref,
            version=version,
            agent_hash=agent_hash,
            files=files,
            meta=ManifestMeta(
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=created_by,
            ),
        )
    
    def verify_files(self, base_path: str) -> List[str]:
        """
        Verify all files match their recorded hashes.
        
        Returns list of errors (empty if all files verify).
        """
        errors = []
        
        for file_entry in self.files:
            full_path = os.path.join(base_path, file_entry.path)
            
            if not os.path.exists(full_path):
                errors.append(f"Missing file: {file_entry.path}")
                continue
            
            # Recompute hash
            actual = FileHash.from_file(base_path, file_entry.path)
            
            if actual.hash != file_entry.hash:
                errors.append(
                    f"Hash mismatch for {file_entry.path}: "
                    f"expected {file_entry.hash}, got {actual.hash}"
                )
            
            if actual.size != file_entry.size:
                errors.append(
                    f"Size mismatch for {file_entry.path}: "
                    f"expected {file_entry.size}, got {actual.size}"
                )
        
        return errors
    
    def verify_agent_hash(self) -> bool:
        """Verify the agent_hash matches the computed hash from files."""
        computed = self.compute_agent_hash(self.files)
        return computed == self.agent_hash
