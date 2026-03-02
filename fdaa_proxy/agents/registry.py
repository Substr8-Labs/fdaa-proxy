"""
FDAA Agent Registry

Core registry logic: create, update, delete, spawn agents.
Integrates with OpenClaw for agent execution.
"""

import os
import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .models import (
    Agent, AgentVersion, AgentPersona, PersonaFile,
    AgentCreate, AgentUpdate, AgentRollback,
    SpawnRequest, SpawnResult
)
from .storage import AgentStorage

logger = logging.getLogger("fdaa.agents.registry")


class AgentRegistry:
    """
    FDAA Agent Registry
    
    Manages agent lifecycle and spawning.
    Agents are defined by persona files and versioned with hashes.
    """
    
    def __init__(
        self,
        storage: AgentStorage = None,
        openclaw_url: str = None,
        openclaw_password: str = None,
    ):
        self.storage = storage or AgentStorage()
        self.openclaw_url = openclaw_url or os.environ.get(
            "OPENCLAW_URL", "http://localhost:18789"
        )
        self.openclaw_password = openclaw_password or os.environ.get(
            "OPENCLAW_PASSWORD", ""
        )
        
        logger.info(f"Agent Registry initialized (OpenClaw: {self.openclaw_url})")
    
    # =========================================================================
    # Agent CRUD
    # =========================================================================
    
    def create(self, request: AgentCreate) -> Agent:
        """Create a new agent."""
        # Build persona
        persona = AgentPersona(files=request.files)
        agent_hash = persona.compute_hash()
        system_prompt = persona.compile_system_prompt()
        persona.system_prompt = system_prompt
        
        # Create initial version
        version = AgentVersion(
            version=1,
            hash=agent_hash,
            persona=persona,
            created_by=request.created_by,
            commit_message=request.commit_message or "Initial version",
        )
        
        # Build agent
        agent = Agent(
            id=request.id,
            name=request.name,
            description=request.description,
            current_version=1,
            current_hash=agent_hash,
            created_by=request.created_by,
            allowed_tools=request.allowed_tools,
            allowed_spawners=request.allowed_spawners,
            versions=[version],
        )
        
        # Store
        return self.storage.create(agent)
    
    def get(self, agent_id: str, include_versions: bool = False) -> Optional[Agent]:
        """Get an agent by ID."""
        return self.storage.get(agent_id, include_versions=include_versions)
    
    def list(self, limit: int = 100, offset: int = 0) -> List[Agent]:
        """List all agents."""
        return self.storage.list(limit=limit, offset=offset)
    
    def update(self, agent_id: str, request: AgentUpdate) -> Optional[Agent]:
        """Update an agent (creates new version if files changed)."""
        agent = self.storage.get(agent_id)
        if not agent:
            return None
        
        # If files provided, create new version
        if request.files:
            persona = AgentPersona(files=request.files)
            new_hash = persona.compute_hash()
            
            # Check if actually changed
            if new_hash == agent.current_hash:
                logger.info(f"No changes to agent {agent_id} (same hash)")
                # Still update metadata if provided
                if request.name or request.description or request.allowed_tools:
                    # Fetch current version to reuse
                    current = self.storage.get_current_version(agent_id)
                    return self.storage.update(
                        agent_id,
                        current,
                        name=request.name,
                        description=request.description,
                        allowed_tools=request.allowed_tools,
                        allowed_spawners=request.allowed_spawners,
                    )
                return agent
            
            # New version
            persona.system_prompt = persona.compile_system_prompt()
            new_version = AgentVersion(
                version=0,  # Will be set by storage
                hash=new_hash,
                persona=persona,
                created_by=request.updated_by,
                commit_message=request.commit_message,
            )
            
            return self.storage.update(
                agent_id,
                new_version,
                name=request.name,
                description=request.description,
                allowed_tools=request.allowed_tools,
                allowed_spawners=request.allowed_spawners,
            )
        
        # Metadata-only update (no new version)
        current = self.storage.get_current_version(agent_id)
        if not current:
            return None
            
        # Update agent directly without version bump
        return self.storage.update(
            agent_id,
            current,
            name=request.name,
            description=request.description,
            allowed_tools=request.allowed_tools,
            allowed_spawners=request.allowed_spawners,
        )
    
    def delete(self, agent_id: str) -> bool:
        """Delete an agent."""
        return self.storage.delete(agent_id)
    
    def rollback(self, agent_id: str, request: AgentRollback) -> Optional[Agent]:
        """Rollback to a previous version."""
        target_version = self.storage.get_version(agent_id, request.version)
        if not target_version:
            return None
        
        # Create new version with old persona (preserves history)
        new_version = AgentVersion(
            version=0,  # Will be set by storage
            hash=target_version.hash,
            persona=target_version.persona,
            created_by=request.rolled_back_by,
            commit_message=f"Rollback to v{request.version}: {request.reason or 'No reason provided'}",
        )
        
        return self.storage.update(agent_id, new_version)
    
    def get_version(self, agent_id: str, version: int) -> Optional[AgentVersion]:
        """Get a specific version."""
        return self.storage.get_version(agent_id, version)
    
    def list_versions(self, agent_id: str) -> List[AgentVersion]:
        """List all versions of an agent."""
        agent = self.storage.get(agent_id, include_versions=True)
        if not agent:
            return []
        return agent.versions
    
    # =========================================================================
    # Spawning
    # =========================================================================
    
    async def spawn(self, request: SpawnRequest) -> SpawnResult:
        """
        Spawn an agent session.
        
        1. Fetch agent and version from registry
        2. Compile system prompt
        3. Call OpenClaw to spawn session
        4. Log spawn event for audit
        
        Note: OpenClaw doesn't expose a REST API for spawning. This method
        is designed for future integration. For now, use get_spawn_payload()
        to get the persona and handle spawning through your app's existing
        OpenClaw integration.
        """
        # Get agent
        agent = self.storage.get(request.agent_id)
        if not agent:
            return SpawnResult(
                success=False,
                agent_id=request.agent_id,
                agent_hash="",
                version=0,
                error=f"Agent '{request.agent_id}' not found",
            )
        
        # Get version
        version_num = request.version or agent.current_version
        version = self.storage.get_version(request.agent_id, version_num)
        if not version:
            return SpawnResult(
                success=False,
                agent_id=request.agent_id,
                agent_hash=agent.current_hash,
                version=version_num,
                error=f"Version {version_num} not found",
            )
        
        # Compile system prompt
        system_prompt = version.persona.system_prompt or version.persona.compile_system_prompt()
        
        # Spawn via OpenClaw
        try:
            async with httpx.AsyncClient() as client:
                # Build spawn request
                spawn_payload = {
                    "task": request.message or "Hello, I am ready to assist.",
                    "label": request.session_label or f"agent:{request.agent_id}",
                    "runTimeoutSeconds": request.timeout_seconds,
                }
                
                # Add model override if specified
                if request.model:
                    spawn_payload["model"] = request.model
                
                # Call OpenClaw sessions_spawn endpoint
                # Note: OpenClaw gateway API uses /api/v1/sessions/spawn
                headers = {}
                if self.openclaw_password:
                    headers["Authorization"] = f"Bearer {self.openclaw_password}"
                
                # Inject system prompt as part of the task
                # OpenClaw spawns use agentId context, so we prepend the persona
                task_with_persona = f"""[AGENT IDENTITY]
{system_prompt}

[END AGENT IDENTITY]

{request.message or "You are now active. Await instructions."}"""
                
                spawn_payload["task"] = task_with_persona
                
                response = await client.post(
                    f"{self.openclaw_url}/api/v1/sessions/spawn",
                    json=spawn_payload,
                    headers=headers,
                    timeout=30.0,
                )
                
                if response.status_code != 200:
                    error_msg = f"OpenClaw spawn failed: {response.status_code} - {response.text}"
                    self.storage.log_spawn(
                        agent_id=request.agent_id,
                        version=version_num,
                        hash=version.hash,
                        spawned_by=request.spawned_by,
                        success=False,
                        error=error_msg,
                    )
                    return SpawnResult(
                        success=False,
                        agent_id=request.agent_id,
                        agent_hash=version.hash,
                        version=version_num,
                        error=error_msg,
                    )
                
                result = response.json()
                session_id = result.get("sessionKey") or result.get("session_id")
                agent_response = result.get("response") or result.get("result")
                
                # Log success
                log_id = self.storage.log_spawn(
                    agent_id=request.agent_id,
                    version=version_num,
                    hash=version.hash,
                    session_id=session_id,
                    spawned_by=request.spawned_by,
                    success=True,
                )
                
                logger.info(
                    f"Spawned agent {request.agent_id} v{version_num} "
                    f"(hash: {version.hash[:16]}..., session: {session_id})"
                )
                
                return SpawnResult(
                    success=True,
                    session_id=session_id,
                    agent_id=request.agent_id,
                    agent_hash=version.hash,
                    version=version_num,
                    response=agent_response,
                    dct_entry_id=str(log_id),
                )
                
        except httpx.RequestError as e:
            error_msg = f"Failed to connect to OpenClaw: {e}"
            self.storage.log_spawn(
                agent_id=request.agent_id,
                version=version_num,
                hash=version.hash,
                spawned_by=request.spawned_by,
                success=False,
                error=error_msg,
            )
            return SpawnResult(
                success=False,
                agent_id=request.agent_id,
                agent_hash=version.hash,
                version=version_num,
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Spawn error: {e}"
            self.storage.log_spawn(
                agent_id=request.agent_id,
                version=version_num,
                hash=version.hash,
                spawned_by=request.spawned_by,
                success=False,
                error=error_msg,
            )
            return SpawnResult(
                success=False,
                agent_id=request.agent_id,
                agent_hash=version.hash,
                version=version_num,
                error=error_msg,
            )
    
    def get_system_prompt(self, agent_id: str, version: int = None) -> Optional[str]:
        """
        Get the compiled system prompt for an agent.
        Useful for inspection without spawning.
        """
        agent = self.storage.get(agent_id)
        if not agent:
            return None
        
        version_num = version or agent.current_version
        ver = self.storage.get_version(agent_id, version_num)
        if not ver:
            return None
        
        return ver.persona.system_prompt or ver.persona.compile_system_prompt()
    
    def get_spawn_payload(self, agent_id: str, version: int = None) -> Optional[Dict[str, Any]]:
        """
        Get the payload needed to spawn an agent.
        
        Use this to integrate with your existing OpenClaw session management.
        Returns the system prompt, hash, and metadata needed for spawning.
        
        Example usage with OpenClaw sessions_spawn tool:
            payload = registry.get_spawn_payload("val")
            sessions_spawn(
                task=payload["system_prompt"] + "\\n\\n" + user_message,
                label=payload["label"],
            )
        """
        agent = self.storage.get(agent_id)
        if not agent:
            return None
        
        version_num = version or agent.current_version
        ver = self.storage.get_version(agent_id, version_num)
        if not ver:
            return None
        
        system_prompt = ver.persona.system_prompt or ver.persona.compile_system_prompt()
        
        return {
            "agent_id": agent_id,
            "version": version_num,
            "hash": ver.hash,
            "system_prompt": system_prompt,
            "label": f"agent:{agent_id}:v{version_num}",
            "allowed_tools": agent.allowed_tools,
            "max_concurrent_sessions": agent.max_concurrent_sessions,
        }
    
    # =========================================================================
    # Audit
    # =========================================================================
    
    def get_spawn_history(self, agent_id: str = None, limit: int = 100) -> List[Dict]:
        """Get spawn history for audit."""
        return self.storage.get_spawn_history(agent_id, limit)
    
    def stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return self.storage.stats()
