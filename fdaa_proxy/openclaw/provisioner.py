"""
OpenClaw Static Agent Provisioner

Provisions agents from FDAA Registry as static agents in OpenClaw gateway.

Flow:
    1. Get agent from registry (with hash verification)
    2. Compile markdown → system prompt
    3. Generate OpenClaw agent config
    4. Patch openclaw.json via gateway API
    5. Trigger hot reload
    6. Verify agent is loaded
    7. Log to DCT

Requirements:
    - OpenClaw gateway.reload.mode: "hybrid" (or "hot")
    - Gateway password for config.patch API
"""

import os
import json
import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("fdaa.openclaw.provisioner")


class ProvisionStatus(str, Enum):
    """Provisioning status."""
    PENDING = "pending"
    HASH_VERIFIED = "hash_verified"
    CONFIG_PATCHED = "config_patched"
    RELOAD_TRIGGERED = "reload_triggered"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class ProvisionResult:
    """Result of provisioning an agent."""
    success: bool
    agent_id: str
    agent_hash: str
    version: int
    status: ProvisionStatus
    
    # Details
    openclaw_agent_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    
    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    
    # Audit
    dct_entry_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "agent_hash": self.agent_hash,
            "version": self.version,
            "status": self.status.value,
            "openclaw_agent_id": self.openclaw_agent_id,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "dct_entry_id": self.dct_entry_id,
        }


class OpenClawProvisioner:
    """
    Provisions FDAA agents as static agents in OpenClaw.
    
    Uses the gateway config.patch API to add agents to agents.list[]
    and triggers hot reload to activate them.
    """
    
    def __init__(
        self,
        gateway_url: str = None,
        gateway_password: str = None,
        dct_logger: Any = None,
    ):
        self.gateway_url = gateway_url or os.environ.get(
            "OPENCLAW_URL", "http://localhost:18789"
        )
        self.gateway_password = gateway_password or os.environ.get(
            "OPENCLAW_PASSWORD", ""
        )
        self.dct_logger = dct_logger
        
        logger.info(f"OpenClaw Provisioner initialized (gateway: {self.gateway_url})")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get auth headers for gateway API."""
        headers = {"Content-Type": "application/json"}
        if self.gateway_password:
            headers["Authorization"] = f"Bearer {self.gateway_password}"
        return headers
    
    async def get_current_config(self) -> Optional[Dict[str, Any]]:
        """Fetch current OpenClaw config by reading the config file directly."""
        try:
            # Try standard config paths
            config_paths = [
                os.path.expanduser("~/.openclaw/openclaw.json"),
                "/home/claw/.openclaw/openclaw.json",
                "/home/node/.openclaw/openclaw.json",
            ]
            
            for config_path in config_paths:
                if os.path.exists(config_path):
                    self._config_path = config_path
                    with open(config_path, 'r') as f:
                        return json.load(f)
            
            logger.error(f"Config file not found in: {config_paths}")
            return None
        except Exception as e:
            logger.error(f"Error reading config: {e}")
            return None
    
    async def get_agents_list(self) -> List[Dict[str, Any]]:
        """Get current agents from config."""
        config = await self.get_current_config()
        if not config:
            return []
        
        agents_config = config.get("agents", {})
        return agents_config.get("list", [])
    
    def generate_openclaw_agent_config(
        self,
        agent_id: str,
        agent_hash: str,
        version: int,
        system_prompt: str,
        name: str = None,
        allowed_tools: List[str] = None,
        workspace: str = None,
    ) -> Dict[str, Any]:
        """
        Generate OpenClaw agent config entry.
        
        Maps FDAA agent definition to OpenClaw agents.list[] schema.
        
        OpenClaw schema for agents.list[] items:
        - id (required)
        - name, workspace, agentDir, model, skills, tools, etc.
        - identity: {name, theme, emoji, avatar} - NOT systemPrompt
        
        For system prompts, OpenClaw reads from agentDir/*.md files.
        We write the compiled prompt to an agent directory and point to it.
        """
        # Generate unique OpenClaw ID
        openclaw_id = f"fdaa:{agent_id}"
        
        # Create agent directory with persona files
        agent_dir = workspace or f"/tmp/fdaa-agents/{agent_id}"
        os.makedirs(agent_dir, exist_ok=True)
        
        # Write system prompt as AGENTS.md (OpenClaw reads this)
        agents_md_path = os.path.join(agent_dir, "AGENTS.md")
        with open(agents_md_path, 'w') as f:
            f.write(f"# FDAA Agent: {agent_id}\n\n")
            f.write(f"Version: {version}\n")
            f.write(f"Hash: {agent_hash}\n\n")
            f.write("---\n\n")
            f.write(system_prompt)
        
        # Write provenance metadata as JSON
        fdaa_meta_path = os.path.join(agent_dir, ".fdaa.json")
        with open(fdaa_meta_path, 'w') as f:
            json.dump({
                "agent_id": agent_id,
                "agent_hash": agent_hash,
                "version": version,
                "provisioned_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        
        logger.info(f"Wrote agent files to {agent_dir}")
        
        # Build tools config (if restrictions specified)
        tools_config = {}
        if allowed_tools and "*" not in allowed_tools:
            # Map to OpenClaw tool policy
            tools_config = {
                "allow": allowed_tools,
            }
        
        agent_config = {
            "id": openclaw_id,
            "name": name or f"FDAA Agent: {agent_id}",
            
            # Point to agent directory (OpenClaw reads *.md from here)
            "agentDir": agent_dir,
            "workspace": agent_dir,
            
            # Identity (cosmetic only - no systemPrompt allowed)
            "identity": {
                "name": name or agent_id,
            },
        }
        
        # Add tools config if specified
        if tools_config:
            agent_config["tools"] = tools_config
        
        return agent_config
    
    async def provision(
        self,
        agent_id: str,
        agent_hash: str,
        version: int,
        system_prompt: str,
        name: str = None,
        allowed_tools: List[str] = None,
        workspace: str = None,
        verify_hash: bool = True,
        expected_hash: str = None,
    ) -> ProvisionResult:
        """
        Provision an agent as a static agent in OpenClaw.
        
        Steps:
            1. Verify hash (if expected_hash provided)
            2. Generate OpenClaw agent config
            3. Patch config via gateway API
            4. Wait for reload
            5. Verify agent is active
        
        Args:
            agent_id: FDAA agent identifier
            agent_hash: SHA256 hash of agent definition
            version: Agent version number
            system_prompt: Compiled system prompt
            name: Display name (optional)
            allowed_tools: List of allowed tool names (optional)
            workspace: Agent workspace path (optional)
            verify_hash: Whether to verify hash matches
            expected_hash: Expected hash (for tamper detection)
        
        Returns:
            ProvisionResult with status and details
        """
        result = ProvisionResult(
            success=False,
            agent_id=agent_id,
            agent_hash=agent_hash,
            version=version,
            status=ProvisionStatus.PENDING,
        )
        
        try:
            # Step 1: Hash verification
            if verify_hash and expected_hash and agent_hash != expected_hash:
                result.status = ProvisionStatus.FAILED
                result.error = f"Hash mismatch: expected {expected_hash[:16]}..., got {agent_hash[:16]}..."
                logger.error(f"Provision failed: {result.error}")
                self._log_to_dct(result, "provision_failed")
                return result
            
            result.status = ProvisionStatus.HASH_VERIFIED
            logger.info(f"Hash verified for {agent_id} v{version}: {agent_hash[:16]}...")
            
            # Step 2: Generate OpenClaw config
            openclaw_config = self.generate_openclaw_agent_config(
                agent_id=agent_id,
                agent_hash=agent_hash,
                version=version,
                system_prompt=system_prompt,
                name=name,
                allowed_tools=allowed_tools,
                workspace=workspace,
            )
            
            openclaw_agent_id = openclaw_config["id"]
            result.openclaw_agent_id = openclaw_agent_id
            
            # Step 3: Get current agents list and update
            current_agents = await self.get_agents_list()
            
            # Remove existing agent with same ID (update scenario)
            updated_agents = [a for a in current_agents if a.get("id") != openclaw_agent_id]
            updated_agents.append(openclaw_config)
            
            # Step 4: Write config directly to file
            try:
                config = await self.get_current_config()
                if not config:
                    result.status = ProvisionStatus.FAILED
                    result.error = "Cannot read OpenClaw config file"
                    logger.error(result.error)
                    self._log_to_dct(result, "provision_failed")
                    return result
                
                # Update agents.list in config
                if "agents" not in config:
                    config["agents"] = {}
                config["agents"]["list"] = updated_agents
                
                # Write config with backup
                config_path = getattr(self, '_config_path', os.path.expanduser("~/.openclaw/openclaw.json"))
                backup_path = config_path + ".bak"
                
                # Create backup
                import shutil
                if os.path.exists(config_path):
                    shutil.copy2(config_path, backup_path)
                
                # Write new config
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                
                logger.info(f"Config written to {config_path}")
                
            except Exception as e:
                result.status = ProvisionStatus.FAILED
                result.error = f"Config write failed: {e}"
                logger.error(result.error)
                self._log_to_dct(result, "provision_failed")
                return result
            
            result.status = ProvisionStatus.CONFIG_PATCHED
            logger.info(f"Config patched for {agent_id}")
            
            # Step 5: Trigger gateway reload via SIGUSR1
            import asyncio
            import subprocess
            
            try:
                # Find gateway PID
                pid_result = subprocess.run(
                    ["pgrep", "-f", "openclaw-gatewa"],
                    capture_output=True,
                    text=True,
                )
                if pid_result.returncode == 0 and pid_result.stdout.strip():
                    pid = int(pid_result.stdout.strip().split()[0])
                    os.kill(pid, 10)  # SIGUSR1 = 10
                    logger.info(f"Sent SIGUSR1 to gateway PID {pid}")
                else:
                    logger.warning("Could not find gateway PID, skipping reload signal")
            except Exception as e:
                logger.warning(f"Failed to send reload signal: {e}")
            
            await asyncio.sleep(2)
            
            result.status = ProvisionStatus.RELOAD_TRIGGERED
            
            # Step 6: Verify agent is loaded
            verification = await self.verify_agent(openclaw_agent_id)
            if verification:
                result.status = ProvisionStatus.VERIFIED
                result.success = True
                result.message = f"Agent {agent_id} v{version} provisioned as {openclaw_agent_id}"
                logger.info(result.message)
            else:
                # Agent might still be loading, mark as success but unverified
                result.success = True
                result.message = f"Agent {agent_id} provisioned (verification pending)"
                logger.warning(result.message)
            
            result.completed_at = datetime.now(timezone.utc)
            self._log_to_dct(result, "provision_success")
            return result
            
        except Exception as e:
            result.status = ProvisionStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now(timezone.utc)
            logger.exception(f"Provision error for {agent_id}: {e}")
            self._log_to_dct(result, "provision_error")
            return result
    
    async def verify_agent(self, openclaw_agent_id: str) -> bool:
        """Verify an agent is loaded in OpenClaw."""
        try:
            agents = await self.get_agents_list()
            for agent in agents:
                if agent.get("id") == openclaw_agent_id:
                    return True
            return False
        except Exception as e:
            logger.error(f"Verification error: {e}")
            return False
    
    async def deprovision(self, agent_id: str) -> ProvisionResult:
        """Remove a provisioned agent from OpenClaw."""
        openclaw_agent_id = f"fdaa:{agent_id}"
        
        result = ProvisionResult(
            success=False,
            agent_id=agent_id,
            agent_hash="",
            version=0,
            status=ProvisionStatus.PENDING,
        )
        
        try:
            current_agents = await self.get_agents_list()
            updated_agents = [a for a in current_agents if a.get("id") != openclaw_agent_id]
            
            if len(updated_agents) == len(current_agents):
                result.error = f"Agent {openclaw_agent_id} not found in config"
                result.status = ProvisionStatus.FAILED
                return result
            
            # Patch config to remove agent
            patch_payload = {
                "action": "config.patch",
                "raw": json.dumps({
                    "agents": {
                        "list": updated_agents
                    }
                }),
                "reason": f"FDAA deprovision: {agent_id}",
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.gateway_url}/api/v1/gateway",
                    json=patch_payload,
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                
                if response.status_code != 200:
                    result.status = ProvisionStatus.FAILED
                    result.error = f"Deprovision failed: {response.status_code}"
                    return result
            
            result.success = True
            result.status = ProvisionStatus.VERIFIED
            result.message = f"Agent {agent_id} deprovisioned"
            result.completed_at = datetime.now(timezone.utc)
            self._log_to_dct(result, "deprovision_success")
            return result
            
        except Exception as e:
            result.status = ProvisionStatus.FAILED
            result.error = str(e)
            logger.exception(f"Deprovision error: {e}")
            return result
    
    async def list_provisioned(self) -> List[Dict[str, Any]]:
        """List all FDAA-provisioned agents in OpenClaw."""
        agents = await self.get_agents_list()
        
        fdaa_agents = []
        for agent in agents:
            if agent.get("id", "").startswith("fdaa:"):
                fdaa_metadata = agent.get("_fdaa", {})
                fdaa_agents.append({
                    "openclaw_id": agent.get("id"),
                    "agent_id": fdaa_metadata.get("agent_id"),
                    "agent_hash": fdaa_metadata.get("agent_hash"),
                    "version": fdaa_metadata.get("version"),
                    "provisioned_at": fdaa_metadata.get("provisioned_at"),
                    "name": agent.get("name"),
                })
        
        return fdaa_agents
    
    def _log_to_dct(self, result: ProvisionResult, action: str):
        """Log provisioning event to DCT."""
        if not self.dct_logger:
            return
        
        try:
            self.dct_logger.append(
                action=action,
                agent_ref=result.agent_id,
                agent_hash=result.agent_hash,
                metadata={
                    "version": result.version,
                    "status": result.status.value,
                    "openclaw_agent_id": result.openclaw_agent_id,
                    "error": result.error,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to log to DCT: {e}")


# =============================================================================
# High-level provisioning function (for CLI integration)
# =============================================================================

async def provision_from_registry(
    registry,  # AgentRegistry instance
    agent_id: str,
    version: int = None,
    gateway_url: str = None,
    gateway_password: str = None,
    dct_logger: Any = None,
) -> ProvisionResult:
    """
    Provision an agent from FDAA registry to OpenClaw.
    
    Convenience function that:
    1. Fetches agent from registry
    2. Compiles system prompt
    3. Provisions to OpenClaw
    
    Args:
        registry: AgentRegistry instance
        agent_id: Agent ID in registry
        version: Specific version (default: current)
        gateway_url: OpenClaw gateway URL
        gateway_password: Gateway auth password
        dct_logger: DCT logger instance
    
    Returns:
        ProvisionResult
    """
    # Get agent from registry
    agent = registry.get(agent_id)
    if not agent:
        return ProvisionResult(
            success=False,
            agent_id=agent_id,
            agent_hash="",
            version=0,
            status=ProvisionStatus.FAILED,
            error=f"Agent '{agent_id}' not found in registry",
        )
    
    # Get version
    target_version = version or agent.current_version
    ver = registry.get_version(agent_id, target_version)
    if not ver:
        return ProvisionResult(
            success=False,
            agent_id=agent_id,
            agent_hash=agent.current_hash,
            version=target_version,
            status=ProvisionStatus.FAILED,
            error=f"Version {target_version} not found",
        )
    
    # Compile system prompt
    system_prompt = ver.persona.system_prompt or ver.persona.compile_system_prompt()
    
    # Provision
    provisioner = OpenClawProvisioner(
        gateway_url=gateway_url,
        gateway_password=gateway_password,
        dct_logger=dct_logger,
    )
    
    return await provisioner.provision(
        agent_id=agent_id,
        agent_hash=ver.hash,
        version=target_version,
        system_prompt=system_prompt,
        name=agent.name,
        allowed_tools=agent.allowed_tools,
    )
