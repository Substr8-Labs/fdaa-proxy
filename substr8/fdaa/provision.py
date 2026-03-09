"""
FDAA Agent Provisioning

Provisions agents into OpenClaw runtime:
1. Load agent spec from path or registry
2. Verify hash matches (if from registry)
3. Write workspace files
4. Patch OpenClaw config with new agent
5. Return provision result with DCT-compatible hashes
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..schemas import (
    AgentSpec,
    Manifest,
    FileHash,
    ACCPolicy,
)


@dataclass
class ProvisionResult:
    """Result of agent provisioning."""
    success: bool
    agent_id: str
    agent_ref: str
    agent_version: str
    agent_hash: str
    workspace_path: str
    policy_hash: str
    errors: List[str]
    warnings: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "agent_ref": self.agent_ref,
            "agent_version": self.agent_version,
            "agent_hash": self.agent_hash,
            "workspace_path": self.workspace_path,
            "policy_hash": self.policy_hash,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def load_agent_spec(path: Path) -> AgentSpec:
    """Load agent spec from agent.yaml in the given path."""
    spec_file = path / "agent.yaml"
    if not spec_file.exists():
        raise FileNotFoundError(f"agent.yaml not found in {path}")
    return AgentSpec.from_file(str(spec_file))


def compute_manifest(agent_path: Path, spec: AgentSpec) -> Manifest:
    """Compute manifest for agent directory."""
    return Manifest.from_directory(
        path=str(agent_path),
        agent_ref=spec.metadata.full_name,
        version=spec.metadata.version,
        include_patterns=[
            "agent.yaml",
            "*.md",
            "tools/*.yaml",
            "skills/*.yaml",
        ],
    )


def create_acc_policy(spec: AgentSpec) -> ACCPolicy:
    """Create ACC policy from agent spec."""
    return ACCPolicy.from_agent_spec(
        agent_ref=spec.metadata.full_name,
        version=spec.metadata.version,
        capabilities=spec.capabilities.to_dict(),
    )


def get_openclaw_config_path() -> Path:
    """Get the OpenClaw config file path."""
    # Check OPENCLAW_CONFIG_PATH env var
    if env_path := os.environ.get("OPENCLAW_CONFIG_PATH"):
        return Path(env_path)
    
    # Default location
    return Path.home() / ".openclaw" / "openclaw.json"


def read_openclaw_config() -> Dict[str, Any]:
    """Read current OpenClaw config."""
    config_path = get_openclaw_config_path()
    if not config_path.exists():
        return {}
    
    with open(config_path) as f:
        return json.load(f)


def write_openclaw_config(config: Dict[str, Any]) -> None:
    """Write OpenClaw config."""
    config_path = get_openclaw_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)


def add_agent_to_config(
    config: Dict[str, Any],
    agent_id: str,
    workspace: str,
    agent_hash: str,
    policy_hash: str,
) -> Dict[str, Any]:
    """Add or update an agent in the OpenClaw config."""
    # Ensure agents.list exists
    if "agents" not in config:
        config["agents"] = {}
    if "list" not in config["agents"]:
        config["agents"]["list"] = []
    
    # Check if agent already exists
    existing_idx = None
    for i, agent in enumerate(config["agents"]["list"]):
        if agent.get("id") == agent_id:
            existing_idx = i
            break
    
    # Agent entry with FDAA metadata
    agent_entry = {
        "id": agent_id,
        "workspace": workspace,
        # Store FDAA metadata in annotations for audit
        "identity": {
            "fdaa": {
                "agent_hash": agent_hash,
                "policy_hash": policy_hash,
                "provisioned_at": datetime.now(timezone.utc).isoformat(),
            }
        }
    }
    
    if existing_idx is not None:
        # Update existing
        config["agents"]["list"][existing_idx] = agent_entry
    else:
        # Add new
        config["agents"]["list"].append(agent_entry)
    
    return config


def provision_agent(
    source_path: Path,
    target_workspace: Optional[Path] = None,
    agent_id: Optional[str] = None,
    expected_hash: Optional[str] = None,
    skip_config: bool = False,
    dry_run: bool = False,
) -> ProvisionResult:
    """
    Provision an agent into OpenClaw runtime.
    
    Args:
        source_path: Path to agent directory (with agent.yaml)
        target_workspace: Where to install agent (default: ~/.openclaw/workspace-{agent_id})
        agent_id: Override agent ID (default: from spec)
        expected_hash: Expected agent hash for verification
        skip_config: Don't modify OpenClaw config
        dry_run: Validate but don't make changes
    
    Returns:
        ProvisionResult with success status and hashes
    """
    errors: List[str] = []
    warnings: List[str] = []
    
    # 1. Load agent spec
    try:
        spec = load_agent_spec(source_path)
    except Exception as e:
        return ProvisionResult(
            success=False,
            agent_id="",
            agent_ref="",
            agent_version="",
            agent_hash="",
            workspace_path="",
            policy_hash="",
            errors=[f"Failed to load agent spec: {e}"],
            warnings=[],
        )
    
    # Validate spec
    spec_errors = spec.validate()
    if spec_errors:
        errors.extend(spec_errors)
        return ProvisionResult(
            success=False,
            agent_id="",
            agent_ref=spec.metadata.full_name,
            agent_version=spec.metadata.version,
            agent_hash="",
            workspace_path="",
            policy_hash="",
            errors=errors,
            warnings=warnings,
        )
    
    # 2. Compute manifest and verify hash
    manifest = compute_manifest(source_path, spec)
    
    if expected_hash and manifest.agent_hash != expected_hash:
        return ProvisionResult(
            success=False,
            agent_id="",
            agent_ref=spec.metadata.full_name,
            agent_version=spec.metadata.version,
            agent_hash=manifest.agent_hash,
            workspace_path="",
            policy_hash="",
            errors=[
                f"Hash mismatch: expected {expected_hash}, got {manifest.agent_hash}",
                "Agent files may have been modified since registration",
            ],
            warnings=[],
        )
    
    # 3. Create ACC policy
    policy = create_acc_policy(spec)
    
    # 4. Determine agent ID and workspace path
    resolved_agent_id = agent_id or spec.metadata.name
    
    if target_workspace:
        workspace_path = target_workspace
    else:
        workspace_path = Path.home() / ".openclaw" / f"workspace-{resolved_agent_id}"
    
    # 5. Copy workspace files (if not dry run)
    if not dry_run:
        if workspace_path.exists():
            warnings.append(f"Workspace {workspace_path} exists, will be updated")
        
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # Copy all files from source
        for file_hash in manifest.files:
            src = source_path / file_hash.path
            dst = workspace_path / file_hash.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        
        # Write manifest
        manifest_path = workspace_path / ".fdaa" / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest.to_json())
        
        # Write policy
        policy_path = workspace_path / ".fdaa" / "policy.json"
        policy_path.write_text(policy.to_json())
    
    # 6. Update OpenClaw config (if not dry run and not skipped)
    if not dry_run and not skip_config:
        try:
            config = read_openclaw_config()
            config = add_agent_to_config(
                config=config,
                agent_id=resolved_agent_id,
                workspace=str(workspace_path),
                agent_hash=manifest.agent_hash,
                policy_hash=policy.policy_hash,
            )
            write_openclaw_config(config)
        except Exception as e:
            errors.append(f"Failed to update OpenClaw config: {e}")
    
    return ProvisionResult(
        success=len(errors) == 0,
        agent_id=resolved_agent_id,
        agent_ref=spec.metadata.full_name,
        agent_version=spec.metadata.version,
        agent_hash=manifest.agent_hash,
        workspace_path=str(workspace_path),
        policy_hash=policy.policy_hash,
        errors=errors,
        warnings=warnings,
    )


def verify_provisioned_agent(agent_id: str) -> Dict[str, Any]:
    """
    Verify a provisioned agent matches its registered hash.
    
    Returns verification result with any mismatches.
    """
    config = read_openclaw_config()
    
    # Find agent in config
    agent_entry = None
    for agent in config.get("agents", {}).get("list", []):
        if agent.get("id") == agent_id:
            agent_entry = agent
            break
    
    if not agent_entry:
        return {
            "verified": False,
            "error": f"Agent '{agent_id}' not found in OpenClaw config",
        }
    
    workspace = Path(agent_entry.get("workspace", ""))
    if not workspace.exists():
        return {
            "verified": False,
            "error": f"Workspace not found: {workspace}",
        }
    
    # Load stored manifest
    manifest_path = workspace / ".fdaa" / "manifest.json"
    if not manifest_path.exists():
        return {
            "verified": False,
            "error": "No FDAA manifest found in workspace",
        }
    
    stored_manifest = Manifest.from_json(manifest_path.read_text())
    
    # Verify files
    file_errors = stored_manifest.verify_files(str(workspace))
    
    # Verify hash
    hash_valid = stored_manifest.verify_agent_hash()
    
    # Get stored hash from config
    fdaa_meta = agent_entry.get("identity", {}).get("fdaa", {})
    config_hash = fdaa_meta.get("agent_hash", "")
    
    hash_match = (config_hash == stored_manifest.agent_hash) if config_hash else True
    
    return {
        "verified": len(file_errors) == 0 and hash_valid and hash_match,
        "agent_id": agent_id,
        "agent_hash": stored_manifest.agent_hash,
        "config_hash": config_hash,
        "hash_valid": hash_valid,
        "hash_match": hash_match,
        "file_errors": file_errors,
    }
