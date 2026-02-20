"""
DCT (Deterministic Computation Trail) Audit Logger

Creates cryptographic audit trails for all gateway operations.
Each entry is hash-chained to the previous, providing tamper detection.

Chain Structure:
    Entry N: { data, prev_hash: hash(Entry N-1), hash: hash(data + prev_hash) }
"""

import json
import hashlib
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Iterator
from pathlib import Path


@dataclass
class DCTEntry:
    """A single entry in the DCT audit chain."""
    id: str
    timestamp: datetime
    event_type: str  # tool_call | approval | policy_check | error
    
    # Event data
    gateway_id: str
    tool: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    
    # Context
    persona: Optional[str] = None
    role: Optional[str] = None
    reasoning: Optional[str] = None
    
    # ACC info
    acc_token_id: Optional[str] = None
    
    # Chain
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None
    
    def compute_hash(self) -> str:
        """Compute hash of this entry."""
        # Serialize deterministically
        data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "gateway_id": self.gateway_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "persona": self.persona,
            "role": self.role,
            "reasoning": self.reasoning,
            "acc_token_id": self.acc_token_id,
            "prev_hash": self.prev_hash,
        }
        
        # Sort keys for determinism
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "gateway_id": self.gateway_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "persona": self.persona,
            "role": self.role,
            "reasoning": self.reasoning,
            "acc_token_id": self.acc_token_id,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCTEntry":
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=data["event_type"],
            gateway_id=data["gateway_id"],
            tool=data.get("tool"),
            arguments=data.get("arguments"),
            result=data.get("result"),
            error=data.get("error"),
            persona=data.get("persona"),
            role=data.get("role"),
            reasoning=data.get("reasoning"),
            acc_token_id=data.get("acc_token_id"),
            prev_hash=data.get("prev_hash"),
            entry_hash=data.get("entry_hash"),
        )


@dataclass
class DCTChain:
    """Verification result for a DCT chain."""
    valid: bool
    entries_checked: int
    first_invalid: Optional[str] = None
    error: Optional[str] = None


class DCTLogger:
    """
    Audit logger with hash chain verification.
    
    Storage backends:
    - sqlite (default): Local SQLite database
    - memory: In-memory (for testing)
    - mongodb: MongoDB collection (optional)
    - postgres: PostgreSQL table (optional)
    """
    
    def __init__(
        self,
        storage: str = "sqlite",
        path: str = "./audit.db",
        mongodb_uri: str = None,
        postgres_uri: str = None,
    ):
        self.storage = storage
        self.path = path
        self._last_hash: Optional[str] = None
        self._entry_count = 0
        
        # Initialize storage
        if storage == "sqlite":
            self._init_sqlite()
        elif storage == "memory":
            self._entries: List[DCTEntry] = []
        elif storage == "mongodb":
            self._init_mongodb(mongodb_uri)
        elif storage == "postgres":
            self._init_postgres(postgres_uri)
    
    def _init_sqlite(self):
        """Initialize SQLite storage."""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS dct_entries (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                gateway_id TEXT NOT NULL,
                tool TEXT,
                arguments TEXT,
                result TEXT,
                error TEXT,
                persona TEXT,
                role TEXT,
                reasoning TEXT,
                acc_token_id TEXT,
                prev_hash TEXT,
                entry_hash TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON dct_entries(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gateway ON dct_entries(gateway_id)
        """)
        self._conn.commit()
        
        # Get last hash
        cursor = self._conn.execute(
            "SELECT entry_hash FROM dct_entries ORDER BY timestamp DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            self._last_hash = row[0]
        
        # Get entry count
        cursor = self._conn.execute("SELECT COUNT(*) FROM dct_entries")
        self._entry_count = cursor.fetchone()[0]
    
    def _init_mongodb(self, uri: str):
        """Initialize MongoDB storage."""
        from pymongo import MongoClient
        client = MongoClient(uri)
        self._db = client.fdaa
        self._collection = self._db.dct_entries
        
        # Create indexes
        self._collection.create_index("timestamp")
        self._collection.create_index("gateway_id")
        
        # Get last hash
        last = self._collection.find_one(sort=[("timestamp", -1)])
        if last:
            self._last_hash = last.get("entry_hash")
        
        self._entry_count = self._collection.count_documents({})
    
    def _init_postgres(self, uri: str):
        """Initialize PostgreSQL storage."""
        # TODO: Implement PostgreSQL backend
        raise NotImplementedError("PostgreSQL backend not yet implemented")
    
    def log(
        self,
        event_type: str,
        gateway_id: str,
        tool: str = None,
        arguments: Dict[str, Any] = None,
        result: Any = None,
        error: str = None,
        persona: str = None,
        role: str = None,
        reasoning: str = None,
        acc_token_id: str = None,
    ) -> DCTEntry:
        """
        Log an event to the audit chain.
        
        Returns the created entry with computed hash.
        """
        self._entry_count += 1
        timestamp = datetime.now(timezone.utc)
        
        entry = DCTEntry(
            id=f"dct_{self._entry_count}_{timestamp.strftime('%Y%m%d%H%M%S%f')}",
            timestamp=timestamp,
            event_type=event_type,
            gateway_id=gateway_id,
            tool=tool,
            arguments=arguments,
            result=result,
            error=error,
            persona=persona,
            role=role,
            reasoning=reasoning,
            acc_token_id=acc_token_id,
            prev_hash=self._last_hash,
        )
        
        # Compute hash
        entry.entry_hash = entry.compute_hash()
        self._last_hash = entry.entry_hash
        
        # Store
        self._store_entry(entry)
        
        return entry
    
    def _store_entry(self, entry: DCTEntry):
        """Store entry in backend."""
        if self.storage == "sqlite":
            self._conn.execute("""
                INSERT INTO dct_entries 
                (id, timestamp, event_type, gateway_id, tool, arguments, result,
                 error, persona, role, reasoning, acc_token_id, prev_hash, entry_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id,
                entry.timestamp.isoformat(),
                entry.event_type,
                entry.gateway_id,
                entry.tool,
                json.dumps(entry.arguments) if entry.arguments else None,
                json.dumps(entry.result) if entry.result else None,
                entry.error,
                entry.persona,
                entry.role,
                entry.reasoning,
                entry.acc_token_id,
                entry.prev_hash,
                entry.entry_hash,
            ))
            self._conn.commit()
        elif self.storage == "memory":
            self._entries.append(entry)
        elif self.storage == "mongodb":
            self._collection.insert_one(entry.to_dict())
    
    def query(
        self,
        gateway_id: str = None,
        event_type: str = None,
        tool: str = None,
        since: datetime = None,
        until: datetime = None,
        limit: int = 100,
    ) -> List[DCTEntry]:
        """Query audit entries."""
        if self.storage == "sqlite":
            return self._query_sqlite(gateway_id, event_type, tool, since, until, limit)
        elif self.storage == "memory":
            return self._query_memory(gateway_id, event_type, tool, since, until, limit)
        elif self.storage == "mongodb":
            return self._query_mongodb(gateway_id, event_type, tool, since, until, limit)
    
    def _query_sqlite(self, gateway_id, event_type, tool, since, until, limit) -> List[DCTEntry]:
        """Query SQLite backend."""
        query = "SELECT * FROM dct_entries WHERE 1=1"
        params = []
        
        if gateway_id:
            query += " AND gateway_id = ?"
            params.append(gateway_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if tool:
            query += " AND tool = ?"
            params.append(tool)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND timestamp <= ?"
            params.append(until.isoformat())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor = self._conn.execute(query, params)
        entries = []
        for row in cursor:
            entries.append(DCTEntry(
                id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                event_type=row[2],
                gateway_id=row[3],
                tool=row[4],
                arguments=json.loads(row[5]) if row[5] else None,
                result=json.loads(row[6]) if row[6] else None,
                error=row[7],
                persona=row[8],
                role=row[9],
                reasoning=row[10],
                acc_token_id=row[11],
                prev_hash=row[12],
                entry_hash=row[13],
            ))
        return entries
    
    def _query_memory(self, gateway_id, event_type, tool, since, until, limit) -> List[DCTEntry]:
        """Query in-memory backend."""
        entries = self._entries
        
        if gateway_id:
            entries = [e for e in entries if e.gateway_id == gateway_id]
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]
        if tool:
            entries = [e for e in entries if e.tool == tool]
        if since:
            entries = [e for e in entries if e.timestamp >= since]
        if until:
            entries = [e for e in entries if e.timestamp <= until]
        
        return sorted(entries, key=lambda e: e.timestamp, reverse=True)[:limit]
    
    def _query_mongodb(self, gateway_id, event_type, tool, since, until, limit) -> List[DCTEntry]:
        """Query MongoDB backend."""
        query = {}
        if gateway_id:
            query["gateway_id"] = gateway_id
        if event_type:
            query["event_type"] = event_type
        if tool:
            query["tool"] = tool
        if since or until:
            query["timestamp"] = {}
            if since:
                query["timestamp"]["$gte"] = since.isoformat()
            if until:
                query["timestamp"]["$lte"] = until.isoformat()
        
        cursor = self._collection.find(query).sort("timestamp", -1).limit(limit)
        return [DCTEntry.from_dict(doc) for doc in cursor]
    
    def verify_chain(self, limit: int = None) -> DCTChain:
        """
        Verify the integrity of the audit chain.
        
        Returns verification result with first invalid entry if found.
        """
        entries = self.query(limit=limit or 10000)
        entries = sorted(entries, key=lambda e: e.timestamp)  # Oldest first
        
        prev_hash = None
        checked = 0
        
        for entry in entries:
            checked += 1
            
            # Check prev_hash matches
            if entry.prev_hash != prev_hash:
                return DCTChain(
                    valid=False,
                    entries_checked=checked,
                    first_invalid=entry.id,
                    error=f"prev_hash mismatch at {entry.id}"
                )
            
            # Recompute hash
            expected_hash = entry.compute_hash()
            if entry.entry_hash != expected_hash:
                return DCTChain(
                    valid=False,
                    entries_checked=checked,
                    first_invalid=entry.id,
                    error=f"entry_hash mismatch at {entry.id}"
                )
            
            prev_hash = entry.entry_hash
        
        return DCTChain(
            valid=True,
            entries_checked=checked
        )
    
    def export(self, format: str = "json") -> str:
        """Export audit log."""
        entries = self.query(limit=100000)
        
        if format == "json":
            return json.dumps([e.to_dict() for e in entries], indent=2)
        elif format == "jsonl":
            return "\n".join(json.dumps(e.to_dict()) for e in entries)
        else:
            raise ValueError(f"Unknown format: {format}")
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get logger statistics."""
        return {
            "storage": self.storage,
            "entry_count": self._entry_count,
            "last_hash": self._last_hash,
        }
