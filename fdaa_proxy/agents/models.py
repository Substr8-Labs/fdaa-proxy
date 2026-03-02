"""
Agent Registry Models

Pydantic models for agent definitions, versions, and spawn requests.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field
import hashlib
import json


class PersonaFile(BaseModel):
    """A single persona file (e.g., SOUL.md, IDENTITY.md)."""
    filename: str
    content: str
    
    def hash(self) -> str:
        """SHA256 hash of the file content."""
        return hashlib.sha256(self.content.encode()).hexdigest()


class AgentPersona(BaseModel):
    """Complete agent persona definition."""
    files: List[PersonaFile] = Field(default_factory=list)
    
    # Computed fields
    system_prompt: Optional[str] = Field(None, description="Compiled system prompt")
    
    def compute_hash(self) -> str:
        """
        Compute deterministic hash of all persona files.
        Sort by filename for consistency.
        """
        sorted_files = sorted(self.files, key=lambda f: f.filename)
        combined = "".join(f"{f.filename}:{f.hash()}" for f in sorted_files)
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def compile_system_prompt(self) -> str:
        """
        Compile persona files into a system prompt.
        Order: SOUL.md first, then IDENTITY.md, then others alphabetically.
        """
        priority_order = ["SOUL.md", "IDENTITY.md", "TOOLS.md", "MEMORY.md"]
        
        def sort_key(f: PersonaFile) -> tuple:
            try:
                idx = priority_order.index(f.filename)
                return (0, idx)
            except ValueError:
                return (1, f.filename)
        
        sorted_files = sorted(self.files, key=sort_key)
        
        sections = []
        for f in sorted_files:
            sections.append(f"## {f.filename}\n\n{f.content}")
        
        return "\n\n---\n\n".join(sections)


class AgentVersion(BaseModel):
    """A specific version of an agent."""
    version: int
    hash: str
    persona: AgentPersona
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    commit_message: Optional[str] = None


class Agent(BaseModel):
    """An agent definition with version history."""
    id: str = Field(..., description="Unique agent identifier (slug)")
    name: str = Field(..., description="Display name")
    description: Optional[str] = None
    
    # Current version
    current_version: int = 1
    current_hash: str
    
    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    
    # Permissions (future: ACC integration)
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"])
    allowed_spawners: List[str] = Field(default_factory=lambda: ["*"])
    max_concurrent_sessions: int = 10
    
    # Versions stored separately
    versions: List[AgentVersion] = Field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "current_version": self.current_version,
            "current_hash": self.current_hash,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "allowed_tools": self.allowed_tools,
            "max_concurrent_sessions": self.max_concurrent_sessions,
            "version_count": len(self.versions),
        }


# =============================================================================
# Request/Response Models
# =============================================================================

class AgentCreate(BaseModel):
    """Request to create a new agent."""
    id: str = Field(..., description="Unique identifier (slug, e.g., 'val', 'ada')")
    name: str = Field(..., description="Display name")
    description: Optional[str] = None
    
    # Persona files
    files: List[PersonaFile] = Field(..., description="Persona files (SOUL.md, etc.)")
    
    # Optional metadata
    created_by: Optional[str] = None
    commit_message: Optional[str] = None
    
    # Permissions
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"])
    allowed_spawners: List[str] = Field(default_factory=lambda: ["*"])


class AgentUpdate(BaseModel):
    """Request to update an agent (creates new version)."""
    files: Optional[List[PersonaFile]] = None
    name: Optional[str] = None
    description: Optional[str] = None
    commit_message: Optional[str] = None
    updated_by: Optional[str] = None
    
    # Permissions
    allowed_tools: Optional[List[str]] = None
    allowed_spawners: Optional[List[str]] = None


class AgentRollback(BaseModel):
    """Request to rollback to a previous version."""
    version: int
    rolled_back_by: Optional[str] = None
    reason: Optional[str] = None


class SpawnRequest(BaseModel):
    """Request to spawn an agent session."""
    agent_id: str = Field(..., description="Agent ID to spawn")
    message: Optional[str] = Field(None, description="Initial message to agent")
    
    # Optional overrides
    version: Optional[int] = Field(None, description="Specific version (default: current)")
    model: Optional[str] = Field(None, description="LLM model override")
    
    # Context
    spawned_by: Optional[str] = None
    session_label: Optional[str] = None
    timeout_seconds: int = 300


class SpawnResult(BaseModel):
    """Result of spawning an agent."""
    success: bool
    session_id: Optional[str] = None
    agent_id: str
    agent_hash: str
    version: int
    
    # Response (if message was provided)
    response: Optional[str] = None
    
    # Error
    error: Optional[str] = None
    
    # Audit
    spawned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dct_entry_id: Optional[str] = None
