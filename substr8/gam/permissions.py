"""
GAM Permissions - W^X Path-Based Access Control

Implements:
- Path-based permission levels
- Human-in-the-loop (HITL) signing requirements
- Write XOR Execute semantics for memory paths
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class PermissionLevel(Enum):
    """Permission levels for memory paths."""
    OPEN = "open"           # Agent can read/write freely
    AGENT_SIGN = "agent"    # Agent must sign writes
    HUMAN_SIGN = "human"    # Human GPG signature required
    READONLY = "readonly"   # No writes allowed


@dataclass
class PathPolicy:
    """Policy for a memory path pattern."""
    pattern: str  # Glob or regex pattern
    permission: PermissionLevel
    description: str = ""
    
    def matches(self, path: str) -> bool:
        """Check if path matches this policy."""
        # Try glob-style matching
        import fnmatch
        if fnmatch.fnmatch(path, self.pattern):
            return True
        
        # Try regex matching
        try:
            if re.match(self.pattern, path):
                return True
        except re.error:
            pass
        
        return False


@dataclass
class PermissionConfig:
    """
    Permission configuration for a GAM repository.
    
    Defines which paths require what level of authorization.
    """
    
    # Default policies (applied in order, first match wins)
    policies: list[PathPolicy] = field(default_factory=list)
    
    # Default permission if no policy matches
    default_permission: PermissionLevel = PermissionLevel.AGENT_SIGN
    
    def __post_init__(self):
        """Set up default policies if none provided."""
        if not self.policies:
            self.policies = self._default_policies()
    
    @staticmethod
    def _default_policies() -> list[PathPolicy]:
        """Default W^X policies for GAM."""
        return [
            # Core identity files require human signature
            PathPolicy(
                pattern="SOUL.md",
                permission=PermissionLevel.HUMAN_SIGN,
                description="Agent's core identity - human must approve changes",
            ),
            PathPolicy(
                pattern="AGENTS.md",
                permission=PermissionLevel.HUMAN_SIGN,
                description="Agent behavior config - human must approve changes",
            ),
            PathPolicy(
                pattern=".gam/identity/*",
                permission=PermissionLevel.HUMAN_SIGN,
                description="Cryptographic identities - human must approve changes",
            ),
            PathPolicy(
                pattern=".gam/config.yaml",
                permission=PermissionLevel.HUMAN_SIGN,
                description="GAM configuration - human must approve changes",
            ),
            
            # User context can be updated by agent (with signature)
            PathPolicy(
                pattern="USER.md",
                permission=PermissionLevel.AGENT_SIGN,
                description="User profile - agent can update with signature",
            ),
            PathPolicy(
                pattern="MEMORY.md",
                permission=PermissionLevel.AGENT_SIGN,
                description="Core memories - agent can update with signature",
            ),
            
            # Daily logs are open (high-volume, low-risk)
            PathPolicy(
                pattern="memory/daily/*",
                permission=PermissionLevel.OPEN,
                description="Daily logs - agent can write freely",
            ),
            
            # Topics and entities require agent signature
            PathPolicy(
                pattern="memory/topics/*",
                permission=PermissionLevel.AGENT_SIGN,
                description="Topic memories - agent signs",
            ),
            PathPolicy(
                pattern="memory/entities/*",
                permission=PermissionLevel.AGENT_SIGN,
                description="Entity profiles - agent signs",
            ),
            
            # Archive is readonly (historical preservation)
            PathPolicy(
                pattern="memory/archive/*",
                permission=PermissionLevel.READONLY,
                description="Archived memories - read only",
            ),
        ]
    
    def get_permission(self, path: str) -> PermissionLevel:
        """Get permission level for a path."""
        for policy in self.policies:
            if policy.matches(path):
                return policy.permission
        return self.default_permission
    
    def requires_human_signature(self, path: str) -> bool:
        """Check if path requires human GPG signature."""
        return self.get_permission(path) == PermissionLevel.HUMAN_SIGN
    
    def requires_agent_signature(self, path: str) -> bool:
        """Check if path requires agent signature."""
        perm = self.get_permission(path)
        return perm in (PermissionLevel.AGENT_SIGN, PermissionLevel.HUMAN_SIGN)
    
    def is_writable(self, path: str) -> bool:
        """Check if path is writable at all."""
        return self.get_permission(path) != PermissionLevel.READONLY
    
    def to_dict(self) -> dict:
        """Serialize configuration."""
        return {
            "default_permission": self.default_permission.value,
            "policies": [
                {
                    "pattern": p.pattern,
                    "permission": p.permission.value,
                    "description": p.description,
                }
                for p in self.policies
            ],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PermissionConfig":
        """Deserialize configuration."""
        return cls(
            default_permission=PermissionLevel(data.get("default_permission", "agent")),
            policies=[
                PathPolicy(
                    pattern=p["pattern"],
                    permission=PermissionLevel(p["permission"]),
                    description=p.get("description", ""),
                )
                for p in data.get("policies", [])
            ],
        )


class PermissionManager:
    """
    Manages permissions for a GAM repository.
    
    Enforces W^X semantics and HITL signing requirements.
    """
    
    def __init__(self, gam_dir: Path):
        self.gam_dir = gam_dir
        self.config_path = gam_dir / "permissions.yaml"
        self.config = self._load_config()
    
    def _load_config(self) -> PermissionConfig:
        """Load permission configuration."""
        if self.config_path.exists():
            import yaml
            with open(self.config_path) as f:
                data = yaml.safe_load(f)
                return PermissionConfig.from_dict(data)
        return PermissionConfig()
    
    def save_config(self):
        """Save permission configuration."""
        import yaml
        with open(self.config_path, "w") as f:
            yaml.dump(self.config.to_dict(), f)
    
    def check_write_permission(
        self,
        path: str,
        actor: str = "agent",
        has_human_signature: bool = False,
        has_agent_signature: bool = False,
    ) -> tuple[bool, str]:
        """
        Check if a write to path is allowed.
        
        Args:
            path: File path (relative to repo root)
            actor: "agent" or "human"
            has_human_signature: Whether human GPG signature is present
            has_agent_signature: Whether agent DID signature is present
        
        Returns:
            (allowed, reason)
        """
        perm = self.config.get_permission(path)
        
        if perm == PermissionLevel.READONLY:
            return False, f"Path '{path}' is read-only"
        
        if perm == PermissionLevel.HUMAN_SIGN:
            if not has_human_signature:
                return False, f"Path '{path}' requires human GPG signature"
            return True, "Human signature verified"
        
        if perm == PermissionLevel.AGENT_SIGN:
            if not (has_agent_signature or has_human_signature):
                return False, f"Path '{path}' requires agent or human signature"
            return True, "Signature verified"
        
        # OPEN permission
        return True, "Path is open for writes"
    
    def get_hitl_paths(self) -> list[PathPolicy]:
        """Get all paths requiring human-in-the-loop signature."""
        return [
            p for p in self.config.policies
            if p.permission == PermissionLevel.HUMAN_SIGN
        ]
    
    def add_policy(
        self,
        pattern: str,
        permission: PermissionLevel,
        description: str = "",
    ):
        """Add a permission policy."""
        policy = PathPolicy(
            pattern=pattern,
            permission=permission,
            description=description,
        )
        
        # Insert at beginning (higher priority)
        self.config.policies.insert(0, policy)
        self.save_config()
    
    def remove_policy(self, pattern: str) -> bool:
        """Remove a permission policy by pattern."""
        for i, policy in enumerate(self.config.policies):
            if policy.pattern == pattern:
                self.config.policies.pop(i)
                self.save_config()
                return True
        return False


# === Enforcement Helpers ===

def require_signature(
    path: str,
    permission_manager: PermissionManager,
    identity_manager: "IdentityManager",  # type: ignore
    agent_name: Optional[str] = None,
) -> tuple[bool, str, Optional[bytes]]:
    """
    Check signature requirements and sign if possible.
    
    Returns:
        (can_proceed, reason, signature_bytes)
    """
    perm = permission_manager.config.get_permission(path)
    
    if perm == PermissionLevel.READONLY:
        return False, "Path is read-only", None
    
    if perm == PermissionLevel.OPEN:
        return True, "No signature required", None
    
    if perm == PermissionLevel.HUMAN_SIGN:
        # Cannot automatically sign - human must intervene
        return False, "Human GPG signature required", None
    
    if perm == PermissionLevel.AGENT_SIGN:
        if agent_name:
            agent = identity_manager.get_agent(agent_name)
            if agent:
                # Sign the path
                signature = agent.sign(path.encode())
                return True, f"Signed by agent '{agent_name}'", signature
        return False, "Agent signature required but no agent specified", None
    
    return False, "Unknown permission level", None
