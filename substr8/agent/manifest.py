"""Agent manifest parsing and validation."""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any


@dataclass
class AgentSource:
    """Source reference for agent definition."""
    type: str = "local"  # local | git | url
    uri: str = ""
    ref: Optional[str] = None


@dataclass
class AgentIdentity:
    """Identity file references."""
    persona: Optional[str] = None
    capabilities: Optional[str] = None
    memory_policy: Optional[str] = None
    
    def get_files(self) -> List[str]:
        """Get list of identity files."""
        files = []
        if self.persona:
            files.append(self.persona)
        if self.capabilities:
            files.append(self.capabilities)
        if self.memory_policy:
            files.append(self.memory_policy)
        return files


@dataclass
class AgentRuntime:
    """Runtime configuration hints."""
    framework: str = "custom"
    entry: Optional[str] = None


@dataclass
class AgentGovernance:
    """Governance policy."""
    allowed_tools: List[str] = field(default_factory=list)
    denied_tools: List[str] = field(default_factory=list)


@dataclass
class AgentMetadata:
    """Optional metadata."""
    author: Optional[str] = None
    org: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    description: Optional[str] = None


@dataclass
class AgentManifest:
    """Complete agent manifest."""
    name: str
    version: str
    identity: AgentIdentity = field(default_factory=AgentIdentity)
    runtime: AgentRuntime = field(default_factory=AgentRuntime)
    governance: AgentGovernance = field(default_factory=AgentGovernance)
    metadata: AgentMetadata = field(default_factory=AgentMetadata)
    
    # Computed fields
    base_path: Optional[Path] = None
    
    def get_all_files(self) -> List[Path]:
        """Get all files that comprise this agent's identity."""
        files = []
        
        # Add identity files
        for rel_path in self.identity.get_files():
            if self.base_path:
                files.append(self.base_path / rel_path)
            else:
                files.append(Path(rel_path))
        
        # Add entry file if specified
        if self.runtime.entry:
            if self.base_path:
                files.append(self.base_path / self.runtime.entry)
            else:
                files.append(Path(self.runtime.entry))
        
        return files
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "version": self.version,
            "identity": {
                "persona": self.identity.persona,
                "capabilities": self.identity.capabilities,
                "memory_policy": self.identity.memory_policy,
            },
            "runtime": {
                "framework": self.runtime.framework,
                "entry": self.runtime.entry,
            },
            "governance": {
                "allowed_tools": self.governance.allowed_tools,
                "denied_tools": self.governance.denied_tools,
            },
            "metadata": {
                "author": self.metadata.author,
                "org": self.metadata.org,
                "tags": self.metadata.tags,
                "description": self.metadata.description,
            }
        }


def load_manifest(path: Path) -> AgentManifest:
    """Load agent manifest from directory or file.
    
    Args:
        path: Path to agent directory or agent.yaml file
        
    Returns:
        Parsed AgentManifest
        
    Raises:
        FileNotFoundError: If manifest not found
        ValueError: If manifest is invalid
    """
    # Resolve path
    if path.is_dir():
        manifest_path = path / "agent.yaml"
        base_path = path
    else:
        manifest_path = path
        base_path = path.parent
    
    if not manifest_path.exists():
        raise FileNotFoundError(f"Agent manifest not found: {manifest_path}")
    
    # Load YAML
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    
    if not data:
        raise ValueError(f"Empty manifest: {manifest_path}")
    
    # Parse required fields
    if "name" not in data:
        raise ValueError("Manifest missing required field: name")
    if "version" not in data:
        raise ValueError("Manifest missing required field: version")
    
    # Build manifest
    manifest = AgentManifest(
        name=data["name"],
        version=data["version"],
        base_path=base_path,
    )
    
    # Parse identity
    if "identity" in data:
        id_data = data["identity"]
        manifest.identity = AgentIdentity(
            persona=id_data.get("persona"),
            capabilities=id_data.get("capabilities"),
            memory_policy=id_data.get("memory_policy"),
        )
    
    # Parse runtime
    if "runtime" in data:
        rt_data = data["runtime"]
        manifest.runtime = AgentRuntime(
            framework=rt_data.get("framework", "custom"),
            entry=rt_data.get("entry"),
        )
    
    # Parse governance
    if "governance" in data:
        gov_data = data["governance"]
        manifest.governance = AgentGovernance(
            allowed_tools=gov_data.get("allowed_tools", []),
            denied_tools=gov_data.get("denied_tools", []),
        )
    
    # Parse metadata
    if "metadata" in data:
        meta_data = data["metadata"]
        manifest.metadata = AgentMetadata(
            author=meta_data.get("author"),
            org=meta_data.get("org"),
            tags=meta_data.get("tags", []),
            description=meta_data.get("description"),
        )
    
    return manifest


def create_manifest_template(name: str, version: str = "1.0.0") -> str:
    """Create a template agent.yaml content."""
    return f"""# Agent Manifest
name: {name}
version: {version}

# Identity files
identity:
  persona: ./SOUL.md
  capabilities: ./CAPS.md
  memory_policy: ./MEMORY.md

# Runtime configuration
runtime:
  framework: openclaw  # openclaw | langchain | crewai | custom
  # entry: ./main.py  # Optional entrypoint

# Governance policies
governance:
  allowed_tools:
    - web_search
    - memory_read
    - memory_write
  denied_tools:
    - shell_exec

# Optional metadata
metadata:
  author: ""
  org: ""
  tags: []
  description: ""
"""
