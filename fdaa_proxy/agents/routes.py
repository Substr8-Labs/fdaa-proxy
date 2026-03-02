"""
Agent Registry API Routes

FastAPI router for agent CRUD and spawn operations.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from .models import (
    Agent, AgentVersion, PersonaFile,
    AgentCreate, AgentUpdate, AgentRollback,
    SpawnRequest, SpawnResult
)
from .registry import AgentRegistry

logger = logging.getLogger("fdaa.agents.routes")


def create_agent_router(registry: AgentRegistry) -> APIRouter:
    """Create FastAPI router for agent operations."""
    
    router = APIRouter(prefix="/v1/agents", tags=["agents"])
    
    # =========================================================================
    # Agent CRUD
    # =========================================================================
    
    @router.get("")
    async def list_agents(
        limit: int = Query(default=100, le=1000),
        offset: int = Query(default=0, ge=0),
    ):
        """List all registered agents."""
        agents = registry.list(limit=limit, offset=offset)
        return {
            "agents": [a.to_dict() for a in agents],
            "count": len(agents),
            "limit": limit,
            "offset": offset,
        }
    
    @router.post("", status_code=201)
    async def create_agent(request: AgentCreate):
        """
        Create a new agent.
        
        Provide persona files (SOUL.md, IDENTITY.md, etc.) and metadata.
        Returns the created agent with computed hash.
        """
        try:
            agent = registry.create(request)
            return {
                "status": "created",
                "agent": agent.to_dict(),
                "hash": agent.current_hash,
            }
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to create agent: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/{agent_id}")
    async def get_agent(
        agent_id: str,
        include_versions: bool = Query(default=False),
    ):
        """Get an agent by ID."""
        agent = registry.get(agent_id, include_versions=include_versions)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        
        result = agent.to_dict()
        if include_versions:
            result["versions"] = [
                {
                    "version": v.version,
                    "hash": v.hash,
                    "created_at": v.created_at.isoformat(),
                    "created_by": v.created_by,
                    "commit_message": v.commit_message,
                }
                for v in agent.versions
            ]
        
        return result
    
    @router.put("/{agent_id}")
    async def update_agent(agent_id: str, request: AgentUpdate):
        """
        Update an agent.
        
        If files are provided, creates a new version.
        If only metadata, updates without version bump.
        """
        agent = registry.update(agent_id, request)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        
        return {
            "status": "updated",
            "agent": agent.to_dict(),
            "hash": agent.current_hash,
        }
    
    @router.delete("/{agent_id}")
    async def delete_agent(agent_id: str):
        """Delete an agent and all its versions."""
        deleted = registry.delete(agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        
        return {"status": "deleted", "agent_id": agent_id}
    
    # =========================================================================
    # Versions
    # =========================================================================
    
    @router.get("/{agent_id}/versions")
    async def list_versions(agent_id: str):
        """List all versions of an agent."""
        versions = registry.list_versions(agent_id)
        if not versions:
            # Check if agent exists
            agent = registry.get(agent_id)
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
            return {"versions": [], "count": 0}
        
        return {
            "agent_id": agent_id,
            "versions": [
                {
                    "version": v.version,
                    "hash": v.hash,
                    "created_at": v.created_at.isoformat(),
                    "created_by": v.created_by,
                    "commit_message": v.commit_message,
                }
                for v in versions
            ],
            "count": len(versions),
        }
    
    @router.get("/{agent_id}/versions/{version}")
    async def get_version(agent_id: str, version: int):
        """Get a specific version of an agent."""
        v = registry.get_version(agent_id, version)
        if not v:
            raise HTTPException(
                status_code=404, 
                detail=f"Version {version} of agent '{agent_id}' not found"
            )
        
        return {
            "agent_id": agent_id,
            "version": v.version,
            "hash": v.hash,
            "created_at": v.created_at.isoformat(),
            "created_by": v.created_by,
            "commit_message": v.commit_message,
            "files": [
                {"filename": f.filename, "hash": f.hash()}
                for f in v.persona.files
            ],
        }
    
    @router.post("/{agent_id}/rollback")
    async def rollback_agent(agent_id: str, request: AgentRollback):
        """Rollback to a previous version."""
        agent = registry.rollback(agent_id, request)
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Cannot rollback: agent or version not found"
            )
        
        return {
            "status": "rolled_back",
            "agent": agent.to_dict(),
            "rolled_back_to": request.version,
            "new_version": agent.current_version,
        }
    
    # =========================================================================
    # System Prompt
    # =========================================================================
    
    @router.get("/{agent_id}/prompt")
    async def get_system_prompt(
        agent_id: str,
        version: Optional[int] = Query(default=None),
    ):
        """
        Get the compiled system prompt for an agent.
        Useful for inspection without spawning.
        """
        prompt = registry.get_system_prompt(agent_id, version)
        if not prompt:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_id}' not found"
            )
        
        agent = registry.get(agent_id)
        return {
            "agent_id": agent_id,
            "version": version or agent.current_version,
            "hash": agent.current_hash,
            "system_prompt": prompt,
        }
    
    @router.get("/{agent_id}/spawn-payload")
    async def get_spawn_payload(
        agent_id: str,
        version: Optional[int] = Query(default=None),
    ):
        """
        Get the payload needed to spawn an agent.
        
        Use this to integrate with your existing OpenClaw session management.
        Returns system prompt, hash, and metadata for spawning.
        """
        payload = registry.get_spawn_payload(agent_id, version)
        if not payload:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_id}' not found"
            )
        
        return payload
    
    # =========================================================================
    # Spawn
    # =========================================================================
    
    class SpawnBody(BaseModel):
        """Optional body for spawn endpoint."""
        message: Optional[str] = None
        version: Optional[int] = None
        model: Optional[str] = None
        spawned_by: Optional[str] = None
        session_label: Optional[str] = None
        timeout_seconds: int = 300

    @router.post("/{agent_id}/spawn")
    async def spawn_agent(agent_id: str, body: SpawnBody = None):
        """
        Spawn an agent session.
        
        Fetches persona from registry, compiles system prompt,
        and spawns via OpenClaw.
        """
        request = SpawnRequest(
            agent_id=agent_id,
            message=body.message if body else None,
            version=body.version if body else None,
            model=body.model if body else None,
            spawned_by=body.spawned_by if body else None,
            session_label=body.session_label if body else None,
            timeout_seconds=body.timeout_seconds if body else 300,
        )
        
        result = await registry.spawn(request)
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        return {
            "status": "spawned",
            "session_id": result.session_id,
            "agent_id": result.agent_id,
            "version": result.version,
            "hash": result.agent_hash,
            "response": result.response,
        }
    
    # Also support spawn at /v1/spawn for convenience
    @router.post("/spawn", include_in_schema=False)
    async def spawn_agent_direct(request: SpawnRequest):
        """Spawn an agent (alternative endpoint)."""
        result = await registry.spawn(request)
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        return {
            "status": "spawned",
            "session_id": result.session_id,
            "agent_id": result.agent_id,
            "version": result.version,
            "hash": result.agent_hash,
            "response": result.response,
        }
    
    # =========================================================================
    # Audit
    # =========================================================================
    
    @router.get("/{agent_id}/spawns")
    async def get_agent_spawn_history(
        agent_id: str,
        limit: int = Query(default=100, le=1000),
    ):
        """Get spawn history for an agent."""
        # Verify agent exists
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        
        history = registry.get_spawn_history(agent_id, limit)
        return {
            "agent_id": agent_id,
            "spawns": history,
            "count": len(history),
        }
    
    return router


def create_spawn_router(registry: AgentRegistry) -> APIRouter:
    """Create separate /v1/spawn router for convenience."""
    
    router = APIRouter(prefix="/v1", tags=["spawn"])
    
    @router.post("/spawn")
    async def spawn(request: SpawnRequest):
        """
        Spawn an agent session.
        
        Provide agent_id and optional message.
        Returns session info and initial response.
        """
        result = await registry.spawn(request)
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        return {
            "status": "spawned",
            "session_id": result.session_id,
            "agent_id": result.agent_id,
            "version": result.version,
            "hash": result.agent_hash,
            "response": result.response,
            "spawned_at": result.spawned_at.isoformat(),
        }
    
    @router.get("/spawns")
    async def get_all_spawn_history(limit: int = Query(default=100, le=1000)):
        """Get all spawn history."""
        history = registry.get_spawn_history(limit=limit)
        return {
            "spawns": history,
            "count": len(history),
        }
    
    @router.get("/registry/stats")
    async def get_registry_stats():
        """Get registry statistics."""
        return registry.stats()
    
    return router
