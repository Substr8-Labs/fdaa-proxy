"""
Work Ledger v2

Proper execution state tracking with:
- Canonical IDs (deterministic, idempotent)
- Promotion state tracking (RIL → GAM)
- Tool transaction integrity
- No fake commit hashes

This replaces the v1 ledger with the architecture from the RIL-GAM sync design.
"""

import json
import sqlite3
import logging
import hashlib
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("fdaa-ril-ledger")


# =============================================================================
# Enums
# =============================================================================

class WorkStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


class ToolTxnStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SYNTHETIC_FAILED = "synthetic_failed"  # CIA injected failure


class PromotionState(str, Enum):
    NONE = "none"
    QUEUED = "queued"
    PROMOTED = "promoted"
    SKIPPED = "skipped"
    FAILED = "failed"


class EventType(str, Enum):
    MESSAGE_RECEIVED = "message_received"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"
    TURN_COMPLETED = "turn_completed"
    DECISION_POINT = "decision_point"
    CRASH_RECOVERY = "crash_recovery"
    CONTEXT_REPAIRED = "context_repaired"
    CAPABILITY_DENIED = "capability_denied"


# =============================================================================
# Canonical ID Generation
# =============================================================================

def canonical_hash(*parts: str) -> str:
    """Generate a canonical hash from parts."""
    combined = "|".join(str(p) for p in parts)
    return hashlib.sha256(combined.encode()).hexdigest()


def make_turn_id(session_id: str, turn_index: int, user_msg_hash: str) -> str:
    """
    Canonical turn ID.
    Deterministic: same inputs = same ID.
    """
    return f"turn:{canonical_hash(session_id, str(turn_index), user_msg_hash)[:16]}"


def make_event_id(turn_id: str, event_type: str, payload_hash: str) -> str:
    """
    Canonical event ID.
    Deterministic: same inputs = same ID.
    """
    return f"evt:{canonical_hash(turn_id, event_type, payload_hash)[:16]}"


def make_tool_txn_id(turn_id: str, tool_name: str, tool_use_id: str) -> str:
    """
    Canonical tool transaction ID.
    Deterministic: same inputs = same ID.
    """
    return f"txn:{canonical_hash(turn_id, tool_name, tool_use_id)[:16]}"


def hash_payload(payload: Any) -> str:
    """Hash a payload deterministically."""
    if payload is None:
        return "sha256:null"
    json_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return f"sha256:{hashlib.sha256(json_str.encode()).hexdigest()}"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class WorkItem:
    """A tracked work item (task/plan)."""
    task_id: str
    session_id: str
    status: WorkStatus
    intent: str
    plan_json: Optional[str] = None
    current_step: int = 0
    context_snapshot_hash: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "intent": self.intent,
            "plan_json": self.plan_json,
            "current_step": self.current_step,
            "context_snapshot_hash": self.context_snapshot_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class Event:
    """A RIL event with promotion tracking."""
    event_id: str  # Canonical ID
    turn_id: str
    event_type: EventType
    event_ts: str
    payload_json: str
    payload_hash: str
    agent_ref: str
    session_id: Optional[str] = None
    attention: float = 0.5  # 0..1 importance score
    promotion_state: PromotionState = PromotionState.NONE
    gam_commit: Optional[str] = None  # Real git SHA when promoted
    gam_path: Optional[str] = None  # File path in GAM repo
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "event_type": self.event_type.value,
            "event_ts": self.event_ts,
            "payload_hash": self.payload_hash,
            "agent_ref": self.agent_ref,
            "session_id": self.session_id,
            "attention": self.attention,
            "promotion_state": self.promotion_state.value,
            "gam_commit": self.gam_commit,
            "gam_path": self.gam_path,
            "created_at": self.created_at,
        }

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json) if self.payload_json else None


@dataclass
class ToolTransaction:
    """A tool call transaction with integrity tracking."""
    tool_txn_id: str  # Canonical ID
    turn_id: str
    tool_use_id: str  # From Anthropic API
    tool_name: str
    input_hash: str
    status: ToolTxnStatus
    result_hash: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_txn_id": self.tool_txn_id,
            "turn_id": self.turn_id,
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "input_hash": self.input_hash,
            "status": self.status.value,
            "result_hash": self.result_hash,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# =============================================================================
# Ledger v2
# =============================================================================

class WorkLedgerV2:
    """
    RIL Ledger with proper schema.
    
    Three tables:
    - work_items: Task/plan tracking
    - events: All RIL events with promotion state
    - tool_transactions: Tool call integrity
    
    Key improvements over v1:
    - Canonical IDs (deterministic, idempotent)
    - Promotion state tracking (none → queued → promoted)
    - No fake commit hashes
    - Tool transaction state machine
    """

    SCHEMA_VERSION = 2

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path("./data/work_ledger_v2.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._stats = {
            "events_logged": 0,
            "events_promoted": 0,
            "tool_txns_started": 0,
            "tool_txns_completed": 0,
            "tool_txns_failed": 0,
            "tool_txns_synthetic": 0,
        }

    def _init_db(self):
        """Initialize the database schema."""
        conn = sqlite3.connect(str(self.path))
        
        # Work items (task tracking)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS work_items (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT NOT NULL,
                plan_json TEXT,
                current_step INTEGER DEFAULT 0,
                context_snapshot_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_work_session ON work_items(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_work_status ON work_items(status)")

        # Events (all RIL events)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_ts TEXT NOT NULL,
                payload_json TEXT,
                payload_hash TEXT NOT NULL,
                agent_ref TEXT NOT NULL,
                session_id TEXT,
                attention REAL DEFAULT 0.5,
                promotion_state TEXT DEFAULT 'none',
                gam_commit TEXT,
                gam_path TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_turn ON events(turn_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_promotion ON events(promotion_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_session ON events(session_id)")

        # Tool transactions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_transactions (
                tool_txn_id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                tool_use_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                result_hash TEXT,
                error TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_turn ON tool_transactions(turn_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_status ON tool_transactions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_tool_use ON tool_transactions(tool_use_id)")

        # Schema version
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(self.SCHEMA_VERSION))
        )

        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    # =========================================================================
    # Events
    # =========================================================================

    def log_event(
        self,
        turn_id: str,
        event_type: EventType,
        payload: Any,
        agent_ref: str,
        session_id: Optional[str] = None,
        attention: float = 0.5,
    ) -> Event:
        """
        Log an event to the ledger.
        
        Uses canonical IDs for idempotency.
        """
        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True) if payload else "{}"
        payload_hash = hash_payload(payload)
        
        # Canonical event ID
        event_id = make_event_id(turn_id, event_type.value, payload_hash)
        
        event = Event(
            event_id=event_id,
            turn_id=turn_id,
            event_type=event_type,
            event_ts=now,
            payload_json=payload_json,
            payload_hash=payload_hash,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=attention,
            promotion_state=PromotionState.NONE,
            created_at=now,
        )

        conn = self._conn()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO events (
                    event_id, turn_id, event_type, event_ts, payload_json,
                    payload_hash, agent_ref, session_id, attention,
                    promotion_state, gam_commit, gam_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id, event.turn_id, event.event_type.value,
                event.event_ts, event.payload_json, event.payload_hash,
                event.agent_ref, event.session_id, event.attention,
                event.promotion_state.value, event.gam_commit, event.gam_path,
                event.created_at,
            ))
            conn.commit()
            self._stats["events_logged"] += 1
            logger.debug(f"Event logged: {event_id} ({event_type.value})")
        finally:
            conn.close()

        return event

    def get_events_for_promotion(self, limit: int = 100) -> List[Event]:
        """Get events that need promotion (state = none, high attention)."""
        conn = self._conn()
        cursor = conn.execute("""
            SELECT * FROM events
            WHERE promotion_state = 'none'
            ORDER BY attention DESC, event_ts ASC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._event_from_row(row) for row in rows]

    def get_events_queued(self) -> List[Event]:
        """Get events queued for promotion."""
        conn = self._conn()
        cursor = conn.execute("""
            SELECT * FROM events
            WHERE promotion_state = 'queued'
            ORDER BY event_ts ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [self._event_from_row(row) for row in rows]

    def mark_event_queued(self, event_id: str) -> bool:
        """Mark an event as queued for promotion."""
        conn = self._conn()
        cursor = conn.execute("""
            UPDATE events SET promotion_state = 'queued'
            WHERE event_id = ? AND promotion_state = 'none'
        """, (event_id,))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def mark_event_promoted(
        self,
        event_id: str,
        gam_commit: str,
        gam_path: str,
    ) -> bool:
        """Mark an event as promoted to GAM."""
        conn = self._conn()
        cursor = conn.execute("""
            UPDATE events
            SET promotion_state = 'promoted', gam_commit = ?, gam_path = ?
            WHERE event_id = ?
        """, (gam_commit, gam_path, event_id))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        if updated:
            self._stats["events_promoted"] += 1
        return updated

    def mark_event_skipped(self, event_id: str) -> bool:
        """Mark an event as skipped (not worth promoting)."""
        conn = self._conn()
        cursor = conn.execute("""
            UPDATE events SET promotion_state = 'skipped'
            WHERE event_id = ?
        """, (event_id,))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def _event_from_row(self, row: tuple) -> Event:
        return Event(
            event_id=row[0],
            turn_id=row[1],
            event_type=EventType(row[2]),
            event_ts=row[3],
            payload_json=row[4],
            payload_hash=row[5],
            agent_ref=row[6],
            session_id=row[7],
            attention=row[8],
            promotion_state=PromotionState(row[9]),
            gam_commit=row[10],
            gam_path=row[11],
            created_at=row[12],
        )

    # =========================================================================
    # Tool Transactions
    # =========================================================================

    def start_tool_txn(
        self,
        turn_id: str,
        tool_use_id: str,
        tool_name: str,
        input_data: Any,
    ) -> ToolTransaction:
        """Start tracking a tool transaction."""
        input_hash = hash_payload(input_data)
        tool_txn_id = make_tool_txn_id(turn_id, tool_name, tool_use_id)
        now = datetime.now(timezone.utc).isoformat()

        txn = ToolTransaction(
            tool_txn_id=tool_txn_id,
            turn_id=turn_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            input_hash=input_hash,
            status=ToolTxnStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        conn = self._conn()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO tool_transactions (
                    tool_txn_id, turn_id, tool_use_id, tool_name, input_hash,
                    status, result_hash, error, duration_ms, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                txn.tool_txn_id, txn.turn_id, txn.tool_use_id, txn.tool_name,
                txn.input_hash, txn.status.value, txn.result_hash, txn.error,
                txn.duration_ms, txn.created_at, txn.updated_at,
            ))
            conn.commit()
            self._stats["tool_txns_started"] += 1
            logger.debug(f"Tool txn started: {tool_txn_id} ({tool_name})")
        finally:
            conn.close()

        return txn

    def complete_tool_txn(
        self,
        tool_txn_id: str,
        result_data: Any,
        duration_ms: Optional[int] = None,
    ) -> bool:
        """Mark a tool transaction as completed."""
        now = datetime.now(timezone.utc).isoformat()
        result_hash = hash_payload(result_data)

        conn = self._conn()
        cursor = conn.execute("""
            UPDATE tool_transactions
            SET status = ?, result_hash = ?, duration_ms = ?, updated_at = ?
            WHERE tool_txn_id = ?
        """, (ToolTxnStatus.COMPLETED.value, result_hash, duration_ms, now, tool_txn_id))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()

        if updated:
            self._stats["tool_txns_completed"] += 1
            logger.debug(f"Tool txn completed: {tool_txn_id}")
        return updated

    def fail_tool_txn(self, tool_txn_id: str, error: str) -> bool:
        """Mark a tool transaction as failed."""
        now = datetime.now(timezone.utc).isoformat()

        conn = self._conn()
        cursor = conn.execute("""
            UPDATE tool_transactions
            SET status = ?, error = ?, updated_at = ?
            WHERE tool_txn_id = ?
        """, (ToolTxnStatus.FAILED.value, error, now, tool_txn_id))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()

        if updated:
            self._stats["tool_txns_failed"] += 1
            logger.debug(f"Tool txn failed: {tool_txn_id}")
        return updated

    def mark_synthetic_failed(self, tool_txn_id: str, reason: str) -> bool:
        """Mark a tool transaction as synthetic-failed (CIA repair)."""
        now = datetime.now(timezone.utc).isoformat()

        conn = self._conn()
        cursor = conn.execute("""
            UPDATE tool_transactions
            SET status = ?, error = ?, updated_at = ?
            WHERE tool_txn_id = ?
        """, (ToolTxnStatus.SYNTHETIC_FAILED.value, f"[CIA] {reason}", now, tool_txn_id))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()

        if updated:
            self._stats["tool_txns_synthetic"] += 1
            logger.info(f"Tool txn synthetic-failed: {tool_txn_id}")
        return updated

    def get_pending_tool_txns(self, turn_id: Optional[str] = None) -> List[ToolTransaction]:
        """Get pending tool transactions (for crash recovery)."""
        conn = self._conn()
        if turn_id:
            cursor = conn.execute("""
                SELECT * FROM tool_transactions
                WHERE status = 'pending' AND turn_id = ?
            """, (turn_id,))
        else:
            cursor = conn.execute("""
                SELECT * FROM tool_transactions WHERE status = 'pending'
            """)
        rows = cursor.fetchall()
        conn.close()
        return [self._txn_from_row(row) for row in rows]

    def get_tool_txn_by_use_id(self, tool_use_id: str) -> Optional[ToolTransaction]:
        """Get a tool transaction by its tool_use_id."""
        conn = self._conn()
        cursor = conn.execute("""
            SELECT * FROM tool_transactions WHERE tool_use_id = ?
        """, (tool_use_id,))
        row = cursor.fetchone()
        conn.close()
        return self._txn_from_row(row) if row else None

    def _txn_from_row(self, row: tuple) -> ToolTransaction:
        return ToolTransaction(
            tool_txn_id=row[0],
            turn_id=row[1],
            tool_use_id=row[2],
            tool_name=row[3],
            input_hash=row[4],
            status=ToolTxnStatus(row[5]),
            result_hash=row[6],
            error=row[7],
            duration_ms=row[8],
            created_at=row[9],
            updated_at=row[10],
        )

    # =========================================================================
    # Work Items (Task Tracking)
    # =========================================================================

    def create_task(
        self,
        session_id: str,
        intent: str,
        plan: Optional[List[str]] = None,
        context_hash: Optional[str] = None,
    ) -> WorkItem:
        """Create a new task/work item."""
        import uuid
        task_id = f"task:{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        item = WorkItem(
            task_id=task_id,
            session_id=session_id,
            status=WorkStatus.PENDING,
            intent=intent,
            plan_json=json.dumps(plan) if plan else None,
            context_snapshot_hash=context_hash,
            created_at=now,
            updated_at=now,
        )

        conn = self._conn()
        conn.execute("""
            INSERT INTO work_items (
                task_id, session_id, status, intent, plan_json,
                current_step, context_snapshot_hash, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.task_id, item.session_id, item.status.value, item.intent,
            item.plan_json, item.current_step, item.context_snapshot_hash,
            item.created_at, item.updated_at, json.dumps(item.metadata),
        ))
        conn.commit()
        conn.close()

        logger.debug(f"Task created: {task_id}")
        return item

    def get_active_tasks(self, session_id: Optional[str] = None) -> List[WorkItem]:
        """Get active (non-completed) tasks."""
        conn = self._conn()
        if session_id:
            cursor = conn.execute("""
                SELECT * FROM work_items
                WHERE session_id = ? AND status NOT IN ('completed', 'failed')
            """, (session_id,))
        else:
            cursor = conn.execute("""
                SELECT * FROM work_items
                WHERE status NOT IN ('completed', 'failed')
            """)
        rows = cursor.fetchall()
        conn.close()
        return [self._work_from_row(row) for row in rows]

    def _work_from_row(self, row: tuple) -> WorkItem:
        return WorkItem(
            task_id=row[0],
            session_id=row[1],
            status=WorkStatus(row[2]),
            intent=row[3],
            plan_json=row[4],
            current_step=row[5],
            context_snapshot_hash=row[6],
            created_at=row[7],
            updated_at=row[8],
            metadata=json.loads(row[9]) if row[9] else {},
        )

    # =========================================================================
    # Stats & Utilities
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get ledger statistics."""
        conn = self._conn()
        
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        promoted_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE promotion_state = 'promoted'"
        ).fetchone()[0]
        pending_txns = conn.execute(
            "SELECT COUNT(*) FROM tool_transactions WHERE status = 'pending'"
        ).fetchone()[0]
        
        conn.close()

        return {
            **self._stats,
            "total_events": event_count,
            "total_promoted": promoted_count,
            "pending_tool_txns": pending_txns,
        }

    def get_session_history(self, session_id: str) -> Dict[str, Any]:
        """Get full history for a session."""
        conn = self._conn()

        # Events
        cursor = conn.execute("""
            SELECT * FROM events WHERE session_id = ? ORDER BY event_ts
        """, (session_id,))
        events = [self._event_from_row(row).to_dict() for row in cursor.fetchall()]

        # Tasks
        cursor = conn.execute("""
            SELECT * FROM work_items WHERE session_id = ? ORDER BY created_at
        """, (session_id,))
        tasks = [self._work_from_row(row).to_dict() for row in cursor.fetchall()]

        conn.close()

        return {
            "session_id": session_id,
            "events": events,
            "tasks": tasks,
        }


# =============================================================================
# Migration Helper
# =============================================================================

def migrate_v1_to_v2(v1_path: Path, v2_path: Path) -> Dict[str, int]:
    """
    Migrate data from v1 ledger to v2.
    
    Maps old schema to new:
    - trigger_events → events (with generated canonical IDs)
    - work_items → work_items (schema similar)
    """
    stats = {"events": 0, "work_items": 0, "skipped": 0}
    
    if not v1_path.exists():
        logger.warning(f"V1 ledger not found: {v1_path}")
        return stats

    v1_conn = sqlite3.connect(str(v1_path))
    v2_ledger = WorkLedgerV2(path=v2_path)

    # Migrate trigger_events → events
    cursor = v1_conn.execute("SELECT * FROM trigger_events ORDER BY timestamp")
    for row in cursor.fetchall():
        try:
            event_type_str = row[1]
            run_id = row[2]
            agent_ref = row[3]
            session_id = row[4]
            timestamp = row[5]
            data_json = row[6]
            
            # Generate turn_id from run_id (best effort)
            turn_id = f"turn:migrated:{canonical_hash(run_id, timestamp)[:12]}"
            
            # Map event type
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                logger.warning(f"Unknown event type: {event_type_str}, skipping")
                stats["skipped"] += 1
                continue

            payload = json.loads(data_json) if data_json else {}
            
            v2_ledger.log_event(
                turn_id=turn_id,
                event_type=event_type,
                payload=payload,
                agent_ref=agent_ref,
                session_id=session_id,
                attention=0.5,  # Default
            )
            stats["events"] += 1
            
        except Exception as e:
            logger.error(f"Migration error for event: {e}")
            stats["skipped"] += 1

    v1_conn.close()
    logger.info(f"Migration complete: {stats}")
    return stats
