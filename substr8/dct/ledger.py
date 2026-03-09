"""
DCT Ledger - SQLite-backed tamper-evident audit log

Provides:
- Append-only storage of DCT entries
- Chain verification (each entry includes prev_hash)
- Export to JSON for audit
- Run-scoped queries
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterator

from ..schemas import (
    DCTEntry,
    DCTAction,
    DCTDecision,
    ActionType,
    GENESIS_HASH,
    verify_chain,
)


# Default ledger location
DEFAULT_LEDGER_PATH = Path.home() / ".fdaa" / "dct_ledger.sqlite"


def get_ledger_path() -> Path:
    """Get the ledger file path, creating directories if needed."""
    path = DEFAULT_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the ledger database schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            entry_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            agent_ref TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            agent_hash TEXT NOT NULL,
            action_json TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            memory_entry_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            
            UNIQUE(run_id, seq)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_run_id ON entries(run_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_ref ON entries(agent_ref)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp ON entries(timestamp)
    """)
    
    conn.commit()


def get_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection, initializing if needed."""
    path = path or get_ledger_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


class DCTLedger:
    """
    SQLite-backed DCT audit ledger.
    
    Provides append-only storage with chain verification.
    """
    
    def __init__(self, path: Optional[Path] = None):
        self.path = path or get_ledger_path()
        self.conn = get_connection(self.path)
    
    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
    
    def __enter__(self) -> "DCTLedger":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    def get_last_hash(self, run_id: str) -> str:
        """Get the hash of the last entry in a run, or GENESIS_HASH if none."""
        cursor = self.conn.execute(
            "SELECT entry_hash FROM entries WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id,)
        )
        row = cursor.fetchone()
        return row["entry_hash"] if row else GENESIS_HASH
    
    def get_next_seq(self, run_id: str) -> int:
        """Get the next sequence number for a run."""
        cursor = self.conn.execute(
            "SELECT MAX(seq) as max_seq FROM entries WHERE run_id = ?",
            (run_id,)
        )
        row = cursor.fetchone()
        max_seq = row["max_seq"]
        # Handle None (no entries) vs 0 (first entry exists)
        return (max_seq + 1) if max_seq is not None else 0
    
    def append(
        self,
        run_id: str,
        agent_ref: str,
        agent_version: str,
        agent_hash: str,
        action: DCTAction,
        decision: DCTDecision,
        memory_entry_hash: Optional[str] = None,
    ) -> DCTEntry:
        """
        Append a new entry to the ledger.
        
        Automatically chains to the previous entry in the run.
        """
        prev_hash = self.get_last_hash(run_id)
        seq = self.get_next_seq(run_id)
        
        entry = DCTEntry.create(
            run_id=run_id,
            seq=seq,
            agent_ref=agent_ref,
            agent_version=agent_version,
            agent_hash=agent_hash,
            action=action,
            decision=decision,
            prev_hash=prev_hash,
            memory_entry_hash=memory_entry_hash,
        )
        
        self.conn.execute(
            """
            INSERT INTO entries (
                entry_id, run_id, seq, timestamp, agent_ref, agent_version,
                agent_hash, action_json, decision_json, prev_hash, entry_hash,
                memory_entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.run_id,
                entry.seq,
                entry.timestamp,
                entry.agent_ref,
                entry.agent_version,
                entry.agent_hash,
                json.dumps(entry.action.to_dict()),
                json.dumps(entry.decision.to_dict()),
                entry.prev_hash,
                entry.entry_hash,
                entry.memory_entry_hash,
            )
        )
        self.conn.commit()
        
        return entry
    
    def get_entry(self, entry_id: str) -> Optional[DCTEntry]:
        """Get a single entry by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM entries WHERE entry_id = ?",
            (entry_id,)
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None
    
    def get_run(self, run_id: str) -> List[DCTEntry]:
        """Get all entries for a run, ordered by sequence."""
        cursor = self.conn.execute(
            "SELECT * FROM entries WHERE run_id = ? ORDER BY seq",
            (run_id,)
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]
    
    def get_agent_entries(
        self,
        agent_ref: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DCTEntry]:
        """Get entries for an agent, ordered by timestamp descending."""
        cursor = self.conn.execute(
            """
            SELECT * FROM entries 
            WHERE agent_ref = ? 
            ORDER BY timestamp DESC 
            LIMIT ? OFFSET ?
            """,
            (agent_ref, limit, offset)
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]
    
    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent runs with summary info."""
        cursor = self.conn.execute(
            """
            SELECT 
                run_id,
                agent_ref,
                MIN(timestamp) as started_at,
                MAX(timestamp) as ended_at,
                COUNT(*) as entry_count,
                MIN(agent_hash) as agent_hash
            FROM entries
            GROUP BY run_id
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def verify_run(self, run_id: str) -> Dict[str, Any]:
        """Verify the chain integrity of a run."""
        entries = self.get_run(run_id)
        
        if not entries:
            return {
                "run_id": run_id,
                "verified": False,
                "error": "Run not found",
            }
        
        errors = verify_chain(entries)
        
        return {
            "run_id": run_id,
            "verified": len(errors) == 0,
            "entry_count": len(entries),
            "first_entry": entries[0].entry_id if entries else None,
            "last_entry": entries[-1].entry_id if entries else None,
            "errors": errors,
        }
    
    def verify_all(self) -> Dict[str, Any]:
        """Verify all runs in the ledger."""
        runs = self.list_runs(limit=1000)
        results = []
        total_errors = 0
        
        for run_info in runs:
            result = self.verify_run(run_info["run_id"])
            results.append(result)
            if not result["verified"]:
                total_errors += len(result.get("errors", []))
        
        return {
            "verified": total_errors == 0,
            "runs_checked": len(runs),
            "runs_with_errors": sum(1 for r in results if not r["verified"]),
            "total_errors": total_errors,
            "results": results,
        }
    
    def export_run(self, run_id: str) -> Dict[str, Any]:
        """Export a run as JSON for audit."""
        entries = self.get_run(run_id)
        
        if not entries:
            return {"error": f"Run {run_id} not found"}
        
        verification = self.verify_run(run_id)
        
        return {
            "run_id": run_id,
            "agent_ref": entries[0].agent_ref,
            "agent_version": entries[0].agent_version,
            "agent_hash": entries[0].agent_hash,
            "entry_count": len(entries),
            "started_at": entries[0].timestamp,
            "ended_at": entries[-1].timestamp,
            "chain_verified": verification["verified"],
            "entries": [e.to_dict() for e in entries],
        }
    
    def export_all(self) -> Iterator[Dict[str, Any]]:
        """Export all runs as a stream of JSON objects."""
        runs = self.list_runs(limit=10000)
        for run_info in runs:
            yield self.export_run(run_info["run_id"])
    
    def _row_to_entry(self, row: sqlite3.Row) -> DCTEntry:
        """Convert a database row to a DCTEntry."""
        return DCTEntry(
            entry_id=row["entry_id"],
            run_id=row["run_id"],
            seq=row["seq"],
            timestamp=row["timestamp"],
            agent_ref=row["agent_ref"],
            agent_version=row["agent_version"],
            agent_hash=row["agent_hash"],
            action=DCTAction.from_dict(json.loads(row["action_json"])),
            decision=DCTDecision.from_dict(json.loads(row["decision_json"])),
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
            memory_entry_hash=row["memory_entry_hash"],
        )
    
    def stats(self) -> Dict[str, Any]:
        """Get ledger statistics."""
        cursor = self.conn.execute("""
            SELECT 
                COUNT(*) as total_entries,
                COUNT(DISTINCT run_id) as total_runs,
                COUNT(DISTINCT agent_ref) as unique_agents,
                MIN(timestamp) as earliest_entry,
                MAX(timestamp) as latest_entry
            FROM entries
        """)
        row = cursor.fetchone()
        
        # Get action type breakdown
        cursor = self.conn.execute("""
            SELECT 
                json_extract(action_json, '$.type') as action_type,
                COUNT(*) as count
            FROM entries
            GROUP BY action_type
        """)
        action_breakdown = {row["action_type"]: row["count"] for row in cursor.fetchall()}
        
        # Get allow/deny breakdown
        cursor = self.conn.execute("""
            SELECT 
                json_extract(decision_json, '$.allowed') as allowed,
                COUNT(*) as count
            FROM entries
            GROUP BY allowed
        """)
        decision_breakdown = {}
        for row in cursor.fetchall():
            key = "allowed" if row["allowed"] else "denied"
            decision_breakdown[key] = row["count"]
        
        return {
            "total_entries": row["total_entries"],
            "total_runs": row["total_runs"],
            "unique_agents": row["unique_agents"],
            "earliest_entry": row["earliest_entry"],
            "latest_entry": row["latest_entry"],
            "action_breakdown": action_breakdown,
            "decision_breakdown": decision_breakdown,
            "ledger_path": str(self.path),
        }
