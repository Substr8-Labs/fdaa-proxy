"""
Agent Registry Storage

SQLite-backed storage for agent definitions and versions.
Designed for simplicity and portability.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .models import Agent, AgentVersion, AgentPersona, PersonaFile

logger = logging.getLogger("fdaa.agents.storage")


class AgentStorage:
    """SQLite storage for agent registry."""
    
    def __init__(self, db_path: str = "./agents.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _conn(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._conn() as conn:
            conn.executescript("""
                -- Agents table
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    current_version INTEGER DEFAULT 1,
                    current_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT,
                    allowed_tools TEXT DEFAULT '["*"]',
                    allowed_spawners TEXT DEFAULT '["*"]',
                    max_concurrent_sessions INTEGER DEFAULT 10
                );
                
                -- Agent versions table
                CREATE TABLE IF NOT EXISTS agent_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    persona_json TEXT NOT NULL,
                    system_prompt TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    commit_message TEXT,
                    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
                    UNIQUE(agent_id, version)
                );
                
                -- Spawn log (for audit)
                CREATE TABLE IF NOT EXISTS spawn_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    session_id TEXT,
                    spawned_by TEXT,
                    spawned_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error TEXT,
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                );
                
                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_versions_agent ON agent_versions(agent_id);
                CREATE INDEX IF NOT EXISTS idx_spawn_agent ON spawn_log(agent_id);
                CREATE INDEX IF NOT EXISTS idx_spawn_time ON spawn_log(spawned_at);
            """)
            logger.info(f"Agent storage initialized at {self.db_path}")
    
    # =========================================================================
    # CRUD Operations
    # =========================================================================
    
    def create(self, agent: Agent) -> Agent:
        """Create a new agent."""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._conn() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT id FROM agents WHERE id = ?", (agent.id,)
            ).fetchone()
            
            if existing:
                raise ValueError(f"Agent '{agent.id}' already exists")
            
            # Insert agent
            conn.execute("""
                INSERT INTO agents 
                (id, name, description, current_version, current_hash, 
                 created_at, updated_at, created_by, allowed_tools, 
                 allowed_spawners, max_concurrent_sessions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent.id,
                agent.name,
                agent.description,
                agent.current_version,
                agent.current_hash,
                now,
                now,
                agent.created_by,
                json.dumps(agent.allowed_tools),
                json.dumps(agent.allowed_spawners),
                agent.max_concurrent_sessions,
            ))
            
            # Insert initial version
            if agent.versions:
                v = agent.versions[0]
                conn.execute("""
                    INSERT INTO agent_versions
                    (agent_id, version, hash, persona_json, system_prompt, 
                     created_at, created_by, commit_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent.id,
                    v.version,
                    v.hash,
                    v.persona.model_dump_json(),
                    v.persona.compile_system_prompt(),
                    v.created_at.isoformat(),
                    v.created_by,
                    v.commit_message,
                ))
        
        logger.info(f"Created agent: {agent.id} (hash: {agent.current_hash[:16]}...)")
        return agent
    
    def get(self, agent_id: str, include_versions: bool = False) -> Optional[Agent]:
        """Get an agent by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            
            if not row:
                return None
            
            versions = []
            if include_versions:
                version_rows = conn.execute(
                    "SELECT * FROM agent_versions WHERE agent_id = ? ORDER BY version DESC",
                    (agent_id,)
                ).fetchall()
                
                for vr in version_rows:
                    persona_data = json.loads(vr["persona_json"])
                    persona = AgentPersona(
                        files=[PersonaFile(**f) for f in persona_data.get("files", [])],
                        system_prompt=vr["system_prompt"]
                    )
                    versions.append(AgentVersion(
                        version=vr["version"],
                        hash=vr["hash"],
                        persona=persona,
                        created_at=datetime.fromisoformat(vr["created_at"]),
                        created_by=vr["created_by"],
                        commit_message=vr["commit_message"],
                    ))
            
            return Agent(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                current_version=row["current_version"],
                current_hash=row["current_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                created_by=row["created_by"],
                allowed_tools=json.loads(row["allowed_tools"]),
                allowed_spawners=json.loads(row["allowed_spawners"]),
                max_concurrent_sessions=row["max_concurrent_sessions"],
                versions=versions,
            )
    
    def list(self, limit: int = 100, offset: int = 0) -> List[Agent]:
        """List all agents."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            
            return [
                Agent(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    current_version=row["current_version"],
                    current_hash=row["current_hash"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    created_by=row["created_by"],
                    allowed_tools=json.loads(row["allowed_tools"]),
                    allowed_spawners=json.loads(row["allowed_spawners"]),
                    max_concurrent_sessions=row["max_concurrent_sessions"],
                    versions=[],
                )
                for row in rows
            ]
    
    def update(self, agent_id: str, new_version: AgentVersion, 
               name: str = None, description: str = None,
               allowed_tools: List[str] = None, 
               allowed_spawners: List[str] = None) -> Optional[Agent]:
        """Update an agent (creates new version)."""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._conn() as conn:
            # Get current agent
            row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            
            if not row:
                return None
            
            new_version_num = row["current_version"] + 1
            new_version.version = new_version_num
            
            # Update agent record
            conn.execute("""
                UPDATE agents SET
                    current_version = ?,
                    current_hash = ?,
                    updated_at = ?,
                    name = COALESCE(?, name),
                    description = COALESCE(?, description),
                    allowed_tools = COALESCE(?, allowed_tools),
                    allowed_spawners = COALESCE(?, allowed_spawners)
                WHERE id = ?
            """, (
                new_version_num,
                new_version.hash,
                now,
                name,
                description,
                json.dumps(allowed_tools) if allowed_tools else None,
                json.dumps(allowed_spawners) if allowed_spawners else None,
                agent_id,
            ))
            
            # Insert new version
            conn.execute("""
                INSERT INTO agent_versions
                (agent_id, version, hash, persona_json, system_prompt,
                 created_at, created_by, commit_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id,
                new_version.version,
                new_version.hash,
                new_version.persona.model_dump_json(),
                new_version.persona.compile_system_prompt(),
                new_version.created_at.isoformat(),
                new_version.created_by,
                new_version.commit_message,
            ))
        
        logger.info(f"Updated agent: {agent_id} -> v{new_version_num} (hash: {new_version.hash[:16]}...)")
        return self.get(agent_id)
    
    def delete(self, agent_id: str) -> bool:
        """Delete an agent and all versions."""
        with self._conn() as conn:
            result = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            deleted = result.rowcount > 0
            if deleted:
                conn.execute("DELETE FROM agent_versions WHERE agent_id = ?", (agent_id,))
                logger.info(f"Deleted agent: {agent_id}")
            return deleted
    
    def get_version(self, agent_id: str, version: int) -> Optional[AgentVersion]:
        """Get a specific version of an agent."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_versions WHERE agent_id = ? AND version = ?",
                (agent_id, version)
            ).fetchone()
            
            if not row:
                return None
            
            persona_data = json.loads(row["persona_json"])
            persona = AgentPersona(
                files=[PersonaFile(**f) for f in persona_data.get("files", [])],
                system_prompt=row["system_prompt"]
            )
            
            return AgentVersion(
                version=row["version"],
                hash=row["hash"],
                persona=persona,
                created_at=datetime.fromisoformat(row["created_at"]),
                created_by=row["created_by"],
                commit_message=row["commit_message"],
            )
    
    def get_current_version(self, agent_id: str) -> Optional[AgentVersion]:
        """Get the current version of an agent."""
        agent = self.get(agent_id)
        if not agent:
            return None
        return self.get_version(agent_id, agent.current_version)
    
    # =========================================================================
    # Spawn Logging
    # =========================================================================
    
    def log_spawn(self, agent_id: str, version: int, hash: str,
                  session_id: str = None, spawned_by: str = None,
                  success: bool = True, error: str = None) -> int:
        """Log a spawn event for audit."""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO spawn_log
                (agent_id, version, hash, session_id, spawned_by, spawned_at, success, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (agent_id, version, hash, session_id, spawned_by, now, int(success), error))
            return cursor.lastrowid
    
    def get_spawn_history(self, agent_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get spawn history for audit."""
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM spawn_log WHERE agent_id = ? ORDER BY spawned_at DESC LIMIT ?",
                    (agent_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM spawn_log ORDER BY spawned_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            
            return [dict(row) for row in rows]
    
    # =========================================================================
    # Stats
    # =========================================================================
    
    def stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._conn() as conn:
            agent_count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            version_count = conn.execute("SELECT COUNT(*) FROM agent_versions").fetchone()[0]
            spawn_count = conn.execute("SELECT COUNT(*) FROM spawn_log").fetchone()[0]
            spawn_success = conn.execute(
                "SELECT COUNT(*) FROM spawn_log WHERE success = 1"
            ).fetchone()[0]
            
            return {
                "agents": agent_count,
                "versions": version_count,
                "spawns_total": spawn_count,
                "spawns_success": spawn_success,
                "spawns_failed": spawn_count - spawn_success,
            }
