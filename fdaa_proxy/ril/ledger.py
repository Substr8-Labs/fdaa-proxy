"""
Work Ledger

Persistent execution state tracking for crash recovery.
Tracks in-flight work items and enables resumption.
"""

import json
import sqlite3
import logging
import hashlib
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("fdaa-ril-ledger")


class WorkStatus(str, Enum):
    """Status of a work item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


@dataclass
class WorkItem:
    """A tracked work item in the ledger."""
    work_id: str
    run_id: str
    agent_ref: str
    status: WorkStatus
    started_at: str
    updated_at: str
    tool: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    error: Optional[str] = None
    context_snapshot: Optional[str] = None  # JSON snapshot for recovery
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "work_id": self.work_id,
            "run_id": self.run_id,
            "agent_ref": self.agent_ref,
            "status": self.status.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "tool": self.tool,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "error": self.error,
            "context_snapshot": self.context_snapshot,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_row(cls, row: tuple) -> "WorkItem":
        return cls(
            work_id=row[0],
            run_id=row[1],
            agent_ref=row[2],
            status=WorkStatus(row[3]),
            started_at=row[4],
            updated_at=row[5],
            tool=row[6],
            input_hash=row[7],
            output_hash=row[8],
            error=row[9],
            context_snapshot=row[10],
            metadata=json.loads(row[11]) if row[11] else {},
        )


class WorkLedger:
    """
    Persistent ledger for tracking execution state.
    
    Enables crash recovery by:
    1. Tracking in-flight work items
    2. Storing context snapshots
    3. Providing recovery queries
    """
    
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path("./data/work_ledger.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        
        # Stats
        self.stats = {
            "total_tracked": 0,
            "completed": 0,
            "failed": 0,
            "recovered": 0,
        }
    
    def _init_db(self):
        """Initialize the SQLite database."""
        conn = sqlite3.connect(str(self.path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS work_items (
                work_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                tool TEXT,
                input_hash TEXT,
                output_hash TEXT,
                error TEXT,
                context_snapshot TEXT,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_id ON work_items(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON work_items(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_ref ON work_items(agent_ref)")
        
        # Trigger events table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trigger_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                run_id TEXT NOT NULL,
                agent_ref TEXT NOT NULL,
                session_id TEXT,
                timestamp TEXT NOT NULL,
                data TEXT,
                commit_hash TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trigger_run ON trigger_events(run_id)")
        
        conn.commit()
        conn.close()
    
    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))
    
    def _hash(self, data: Any) -> str:
        """Create a hash of data."""
        json_str = json.dumps(data, sort_keys=True)
        return f"sha256:{hashlib.sha256(json_str.encode()).hexdigest()}"
    
    def start_work(
        self,
        run_id: str,
        agent_ref: str,
        tool: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        context: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkItem:
        """
        Start tracking a work item.
        
        Called when a tool call begins.
        """
        import uuid
        
        now = datetime.now(timezone.utc).isoformat()
        work_id = f"work-{uuid.uuid4().hex[:12]}"
        
        input_hash = self._hash(input_data) if input_data else None
        context_snapshot = json.dumps(context) if context else None
        
        item = WorkItem(
            work_id=work_id,
            run_id=run_id,
            agent_ref=agent_ref,
            status=WorkStatus.IN_PROGRESS,
            started_at=now,
            updated_at=now,
            tool=tool,
            input_hash=input_hash,
            context_snapshot=context_snapshot,
            metadata=metadata or {},
        )
        
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO work_items (
                work_id, run_id, agent_ref, status, started_at, updated_at,
                tool, input_hash, output_hash, error, context_snapshot, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.work_id, item.run_id, item.agent_ref, item.status.value,
            item.started_at, item.updated_at, item.tool, item.input_hash,
            item.output_hash, item.error, item.context_snapshot,
            json.dumps(item.metadata),
        ))
        conn.commit()
        conn.close()
        
        self.stats["total_tracked"] += 1
        logger.debug(f"Work started: {work_id} for tool {tool}")
        
        return item
    
    def complete_work(
        self,
        work_id: str,
        output_data: Optional[Any] = None,
    ) -> Optional[WorkItem]:
        """Mark a work item as completed."""
        now = datetime.now(timezone.utc).isoformat()
        output_hash = self._hash(output_data) if output_data else None
        
        conn = self._get_conn()
        conn.execute("""
            UPDATE work_items
            SET status = ?, updated_at = ?, output_hash = ?
            WHERE work_id = ?
        """, (WorkStatus.COMPLETED.value, now, output_hash, work_id))
        conn.commit()
        
        cursor = conn.execute(
            "SELECT * FROM work_items WHERE work_id = ?", (work_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            self.stats["completed"] += 1
            logger.debug(f"Work completed: {work_id}")
            return WorkItem.from_row(row)
        return None
    
    def fail_work(
        self,
        work_id: str,
        error: str,
    ) -> Optional[WorkItem]:
        """Mark a work item as failed."""
        now = datetime.now(timezone.utc).isoformat()
        
        conn = self._get_conn()
        conn.execute("""
            UPDATE work_items
            SET status = ?, updated_at = ?, error = ?
            WHERE work_id = ?
        """, (WorkStatus.FAILED.value, now, error, work_id))
        conn.commit()
        
        cursor = conn.execute(
            "SELECT * FROM work_items WHERE work_id = ?", (work_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            self.stats["failed"] += 1
            logger.debug(f"Work failed: {work_id}")
            return WorkItem.from_row(row)
        return None
    
    def get_in_progress(self, run_id: Optional[str] = None) -> List[WorkItem]:
        """Get all in-progress work items."""
        conn = self._get_conn()
        if run_id:
            cursor = conn.execute(
                "SELECT * FROM work_items WHERE status = ? AND run_id = ?",
                (WorkStatus.IN_PROGRESS.value, run_id)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM work_items WHERE status = ?",
                (WorkStatus.IN_PROGRESS.value,)
            )
        rows = cursor.fetchall()
        conn.close()
        return [WorkItem.from_row(row) for row in rows]
    
    def get_recoverable(self) -> List[WorkItem]:
        """
        Get work items that may need recovery.
        
        Returns items that are IN_PROGRESS but may be stale.
        """
        conn = self._get_conn()
        cursor = conn.execute("""
            SELECT * FROM work_items
            WHERE status = ?
            ORDER BY started_at ASC
        """, (WorkStatus.IN_PROGRESS.value,))
        rows = cursor.fetchall()
        conn.close()
        return [WorkItem.from_row(row) for row in rows]
    
    def mark_recovered(self, work_id: str) -> Optional[WorkItem]:
        """Mark a work item as recovered (after crash recovery)."""
        now = datetime.now(timezone.utc).isoformat()
        
        conn = self._get_conn()
        conn.execute("""
            UPDATE work_items
            SET status = ?, updated_at = ?
            WHERE work_id = ?
        """, (WorkStatus.RECOVERED.value, now, work_id))
        conn.commit()
        
        cursor = conn.execute(
            "SELECT * FROM work_items WHERE work_id = ?", (work_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            self.stats["recovered"] += 1
            logger.info(f"Work recovered: {work_id}")
            return WorkItem.from_row(row)
        return None
    
    def log_trigger(self, ctx) -> str:
        """Log a trigger event to the ledger."""
        import uuid
        
        commit_hash = f"sha256:{uuid.uuid4().hex}"
        
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO trigger_events (
                event_type, run_id, agent_ref, session_id, timestamp, data, commit_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ctx.event.value, ctx.run_id, ctx.agent_ref, ctx.session_id,
            ctx.timestamp, json.dumps(ctx.data), commit_hash,
        ))
        conn.commit()
        conn.close()
        
        return commit_hash
    
    def get_run_history(self, run_id: str) -> List[Dict[str, Any]]:
        """Get full history for a run (work items + trigger events)."""
        conn = self._get_conn()
        
        # Get work items
        cursor = conn.execute(
            "SELECT * FROM work_items WHERE run_id = ? ORDER BY started_at",
            (run_id,)
        )
        work_items = [WorkItem.from_row(row).to_dict() for row in cursor.fetchall()]
        
        # Get trigger events
        cursor = conn.execute(
            "SELECT * FROM trigger_events WHERE run_id = ? ORDER BY timestamp",
            (run_id,)
        )
        trigger_events = [{
            "id": row[0],
            "event_type": row[1],
            "run_id": row[2],
            "agent_ref": row[3],
            "session_id": row[4],
            "timestamp": row[5],
            "data": json.loads(row[6]) if row[6] else None,
            "commit_hash": row[7],
        } for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "run_id": run_id,
            "work_items": work_items,
            "trigger_events": trigger_events,
        }
    
    def get_stats(self) -> Dict[str, int]:
        """Get ledger statistics."""
        return self.stats.copy()
