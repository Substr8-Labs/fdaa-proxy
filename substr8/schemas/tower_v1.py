"""
Tower v1 Agent Specification Schema

Defines the canonical format for agent definitions in the Substr8 platform.
This is the "source of truth" for what an agent IS before it's hashed and registered.

Example:
    apiVersion: tower/v1
    kind: Agent
    metadata:
      name: analyst
      namespace: substr8
      version: 1.0.0
    spec:
      persona_files:
        - SOUL.md
        - IDENTITY.md
      capabilities:
        allow:
          - web_search
          - memory_read
          - memory_write
        deny:
          - shell_exec
          - file_write
      memory:
        backend: gam
        types:
          - insight
          - decision
          - reference
      constraints:
        require_citations: true
        max_turns: 10
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import json
import yaml


class MemoryBackend(str, Enum):
    GAM = "gam"
    LOCAL = "local"
    NONE = "none"


@dataclass
class AgentMemoryConfig:
    """Memory configuration for an agent."""
    backend: MemoryBackend = MemoryBackend.GAM
    types: List[str] = field(default_factory=lambda: ["insight", "decision", "reference"])
    retention_days: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend.value,
            "types": self.types,
            "retention_days": self.retention_days,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMemoryConfig":
        return cls(
            backend=MemoryBackend(data.get("backend", "gam")),
            types=data.get("types", ["insight", "decision", "reference"]),
            retention_days=data.get("retention_days"),
        )


@dataclass
class AgentCapabilities:
    """ACC-compatible capability specification."""
    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "allow": self.allow,
            "deny": self.deny,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentCapabilities":
        if isinstance(data, dict):
            return cls(
                allow=data.get("allow", []),
                deny=data.get("deny", []),
            )
        # Legacy format: just a list of allowed tools
        if isinstance(data, list):
            return cls(allow=data, deny=[])
        return cls()


@dataclass
class AgentConstraints:
    """Runtime constraints for agent behavior."""
    max_turns: Optional[int] = None
    require_citations: bool = False
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "require_citations": self.require_citations,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConstraints":
        return cls(
            max_turns=data.get("max_turns"),
            require_citations=data.get("require_citations", False),
            max_tokens=data.get("max_tokens"),
            timeout_seconds=data.get("timeout_seconds"),
        )


@dataclass
class AgentMetadata:
    """Agent identification metadata."""
    name: str
    namespace: str = "default"
    version: str = "0.0.0"
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "labels": self.labels,
            "annotations": self.annotations,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMetadata":
        return cls(
            name=data["name"],
            namespace=data.get("namespace", "default"),
            version=data.get("version", "0.0.0"),
            labels=data.get("labels", {}),
            annotations=data.get("annotations", {}),
        )
    
    @property
    def full_name(self) -> str:
        """Return namespace/name format."""
        return f"{self.namespace}/{self.name}"
    
    @property
    def versioned_name(self) -> str:
        """Return namespace/name@version format."""
        return f"{self.namespace}/{self.name}@{self.version}"


@dataclass
class AgentSpec:
    """
    Complete Tower v1 Agent Specification.
    
    This is the canonical representation of an agent definition.
    It gets hashed by FDAA to create the agent_hash for the registry.
    """
    api_version: str  # "tower/v1"
    kind: str  # "Agent"
    metadata: AgentMetadata
    persona_files: List[str] = field(default_factory=lambda: ["SOUL.md", "IDENTITY.md"])
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    memory: AgentMemoryConfig = field(default_factory=AgentMemoryConfig)
    constraints: AgentConstraints = field(default_factory=AgentConstraints)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": self.metadata.to_dict(),
            "spec": {
                "persona_files": self.persona_files,
                "capabilities": self.capabilities.to_dict(),
                "memory": self.memory.to_dict(),
                "constraints": self.constraints.to_dict(),
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSpec":
        """Parse from dictionary (YAML/JSON loaded)."""
        spec = data.get("spec", {})
        return cls(
            api_version=data.get("apiVersion", "tower/v1"),
            kind=data.get("kind", "Agent"),
            metadata=AgentMetadata.from_dict(data.get("metadata", {})),
            persona_files=spec.get("persona_files", ["SOUL.md", "IDENTITY.md"]),
            capabilities=AgentCapabilities.from_dict(spec.get("capabilities", {})),
            memory=AgentMemoryConfig.from_dict(spec.get("memory", {})),
            constraints=AgentConstraints.from_dict(spec.get("constraints", {})),
        )
    
    @classmethod
    def from_yaml(cls, yaml_str: str) -> "AgentSpec":
        """Parse from YAML string."""
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, path: str) -> "AgentSpec":
        """Load from YAML file."""
        with open(path, 'r') as f:
            return cls.from_yaml(f.read())
    
    def to_yaml(self) -> str:
        """Serialize to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
    
    def validate(self) -> List[str]:
        """Validate the spec and return list of errors (empty if valid)."""
        errors = []
        
        if self.api_version != "tower/v1":
            errors.append(f"Unsupported apiVersion: {self.api_version} (expected tower/v1)")
        
        if self.kind != "Agent":
            errors.append(f"Unsupported kind: {self.kind} (expected Agent)")
        
        if not self.metadata.name:
            errors.append("metadata.name is required")
        
        if not self.persona_files:
            errors.append("spec.persona_files must contain at least one file")
        
        # Check for conflicting capabilities
        conflicts = set(self.capabilities.allow) & set(self.capabilities.deny)
        if conflicts:
            errors.append(f"Conflicting capabilities (both allow and deny): {conflicts}")
        
        return errors
