"""
GAM Database Client

Postgres + pgvector client for multi-tenant memory operations.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import json
import psycopg2
from psycopg2.extras import RealDictCursor, Json


def get_connection_string() -> str:
    """Get database connection string."""
    url = os.environ.get("GAM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    
    if url:
        return url
    
    secrets_path = Path.home() / ".openclaw" / "secrets" / "neon-url.txt"
    if secrets_path.exists():
        return secrets_path.read_text().strip()
    
    raise ValueError("No database URL found. Set GAM_DATABASE_URL environment variable.")


class GAMDatabaseClient:
    """Client for GAM database operations."""
    
    def __init__(self, database_url: str = None):
        self.database_url = database_url or get_connection_string()
        self._conn = None
    
    @property
    def conn(self):
        """Get or create connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
        return self._conn
    
    def close(self):
        """Close connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
    
    # === Tenant Operations ===
    
    def create_tenant(
        self,
        name: str,
        git_repo_url: str = None,
        settings: dict = None,
    ) -> dict:
        """Create a new tenant."""
        tenant_id = uuid4()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO gam_tenants (id, name, git_repo_url, settings)
                VALUES (%s, %s, %s, %s)
                RETURNING *
            """, (str(tenant_id), name, git_repo_url, Json(settings or {})))
            
            self.conn.commit()
            return dict(cur.fetchone())
    
    def get_tenant(self, tenant_id: str) -> Optional[dict]:
        """Get a tenant by ID."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM gam_tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def list_tenants(self) -> list[dict]:
        """List all tenants."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM gam_tenants ORDER BY created_at DESC")
            return [dict(row) for row in cur.fetchall()]
    
    # === Branch Operations ===
    
    def create_branch(
        self,
        tenant_id: str,
        name: str,
        level: str,
        parent_branch: str = None,
        description: str = None,
    ) -> dict:
        """Create a branch record."""
        branch_id = uuid4()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO gam_branches 
                (id, tenant_id, name, level, parent_branch, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (str(branch_id), tenant_id, name, level, parent_branch, description))
            
            self.conn.commit()
            return dict(cur.fetchone())
    
    def get_branch(self, tenant_id: str, name: str) -> Optional[dict]:
        """Get a branch by name."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM gam_branches WHERE tenant_id = %s AND name = %s",
                (tenant_id, name)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def list_branches(self, tenant_id: str, level: str = None) -> list[dict]:
        """List branches for a tenant."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if level:
                cur.execute(
                    "SELECT * FROM gam_branches WHERE tenant_id = %s AND level = %s ORDER BY name",
                    (tenant_id, level)
                )
            else:
                cur.execute(
                    "SELECT * FROM gam_branches WHERE tenant_id = %s ORDER BY level, name",
                    (tenant_id,)
                )
            return [dict(row) for row in cur.fetchall()]
    
    # === Memory Operations ===
    
    def store_memory(
        self,
        tenant_id: str,
        branch: str,
        memory_id: str,
        file_path: str,
        content: str,
        content_hash: str,
        section: str = None,
        source: str = "user",
        confidence: str = "medium",
        classification: str = "private",
        tags: list[str] = None,
        commit_sha: str = None,
        author: str = None,
        embedding: list[float] = None,
    ) -> dict:
        """Store a memory entry."""
        record_id = uuid4()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO gam_memories 
                (id, tenant_id, branch, memory_id, file_path, section, content, 
                 content_hash, source, confidence, classification, tags, 
                 commit_sha, author, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, branch, memory_id) 
                DO UPDATE SET
                    content = EXCLUDED.content,
                    content_hash = EXCLUDED.content_hash,
                    embedding = EXCLUDED.embedding,
                    updated_at = NOW()
                RETURNING *
            """, (
                str(record_id), tenant_id, branch, memory_id, file_path, section,
                content, content_hash, source, confidence, classification,
                tags or [], commit_sha, author, embedding
            ))
            
            self.conn.commit()
            return dict(cur.fetchone())
    
    def search_memories(
        self,
        tenant_id: str,
        branch: str,
        query_embedding: list[float],
        limit: int = 10,
        threshold: float = 0.3,
        classification: str = None,
    ) -> list[dict]:
        """Semantic search for memories."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if classification:
                cur.execute("""
                    SELECT 
                        memory_id, file_path, section, content, tags, classification,
                        1 - (embedding <=> %s::vector) as similarity
                    FROM gam_memories
                    WHERE tenant_id = %s 
                      AND branch = %s
                      AND classification = %s
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (query_embedding, tenant_id, branch, classification, 
                      query_embedding, threshold, query_embedding, limit))
            else:
                cur.execute("""
                    SELECT 
                        memory_id, file_path, section, content, tags, classification,
                        1 - (embedding <=> %s::vector) as similarity
                    FROM gam_memories
                    WHERE tenant_id = %s 
                      AND branch = %s
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (query_embedding, tenant_id, branch, 
                      query_embedding, threshold, query_embedding, limit))
            
            return [dict(row) for row in cur.fetchall()]
    
    def get_memories_by_tags(
        self,
        tenant_id: str,
        branch: str,
        tags: list[str],
        classification: str = None,
    ) -> list[dict]:
        """Get memories matching any of the given tags."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if classification:
                cur.execute("""
                    SELECT * FROM gam_memories
                    WHERE tenant_id = %s AND branch = %s
                      AND classification = %s
                      AND tags && %s
                    ORDER BY created_at DESC
                """, (tenant_id, branch, classification, tags))
            else:
                cur.execute("""
                    SELECT * FROM gam_memories
                    WHERE tenant_id = %s AND branch = %s
                      AND tags && %s
                    ORDER BY created_at DESC
                """, (tenant_id, branch, tags))
            
            return [dict(row) for row in cur.fetchall()]
    
    # === Proposal Operations ===
    
    def create_proposal(
        self,
        tenant_id: str,
        proposal_id: str,
        proposal_type: str,
        target_branch: str,
        title: str,
        source_branch: str = None,
        description: str = None,
        entries: list[dict] = None,
        filters: dict = None,
        created_by: str = "gam",
    ) -> dict:
        """Create a proposal record."""
        record_id = uuid4()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO gam_proposals
                (id, tenant_id, proposal_id, type, source_branch, target_branch,
                 title, description, entries, filters, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                str(record_id), tenant_id, proposal_id, proposal_type,
                source_branch, target_branch, title, description,
                Json(entries or []), Json(filters or {}), created_by
            ))
            
            self.conn.commit()
            return dict(cur.fetchone())
    
    def update_proposal_status(
        self,
        tenant_id: str,
        proposal_id: str,
        status: str,
        reviewed_by: str = None,
        review_notes: str = None,
        commit_sha: str = None,
    ) -> dict:
        """Update proposal status."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status in ("approved", "rejected"):
                cur.execute("""
                    UPDATE gam_proposals
                    SET status = %s, reviewed_by = %s, reviewed_at = NOW(), review_notes = %s
                    WHERE tenant_id = %s AND proposal_id = %s
                    RETURNING *
                """, (status, reviewed_by, review_notes, tenant_id, proposal_id))
            elif status == "merged":
                cur.execute("""
                    UPDATE gam_proposals
                    SET status = %s, commit_sha = %s, merged_at = NOW()
                    WHERE tenant_id = %s AND proposal_id = %s
                    RETURNING *
                """, (status, commit_sha, tenant_id, proposal_id))
            else:
                cur.execute("""
                    UPDATE gam_proposals
                    SET status = %s
                    WHERE tenant_id = %s AND proposal_id = %s
                    RETURNING *
                """, (status, tenant_id, proposal_id))
            
            self.conn.commit()
            row = cur.fetchone()
            return dict(row) if row else None
    
    def list_proposals(
        self,
        tenant_id: str,
        status: str = None,
        target_branch: str = None,
    ) -> list[dict]:
        """List proposals for a tenant."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = "SELECT * FROM gam_proposals WHERE tenant_id = %s"
            params = [tenant_id]
            
            if status:
                query += " AND status = %s"
                params.append(status)
            
            if target_branch:
                query += " AND target_branch = %s"
                params.append(target_branch)
            
            query += " ORDER BY created_at DESC"
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    # === Audit Operations ===
    
    def log_event(
        self,
        tenant_id: str,
        event_type: str,
        actor: str,
        branch: str = None,
        memory_id: str = None,
        proposal_id: str = None,
        details: dict = None,
        trace_id: str = None,
        commit_sha: str = None,
    ) -> dict:
        """Log an audit event."""
        record_id = uuid4()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO gam_audit_log
                (id, tenant_id, event_type, actor, branch, memory_id, 
                 proposal_id, details, trace_id, commit_sha)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                str(record_id), tenant_id, event_type, actor, branch,
                memory_id, proposal_id, Json(details or {}), trace_id, commit_sha
            ))
            
            self.conn.commit()
            return dict(cur.fetchone())
    
    def get_audit_log(
        self,
        tenant_id: str,
        event_type: str = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get audit log entries."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if event_type:
                cur.execute("""
                    SELECT * FROM gam_audit_log
                    WHERE tenant_id = %s AND event_type = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (tenant_id, event_type, limit))
            else:
                cur.execute("""
                    SELECT * FROM gam_audit_log
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (tenant_id, limit))
            
            return [dict(row) for row in cur.fetchall()]
