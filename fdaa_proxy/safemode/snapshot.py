"""
SafeMode Snapshot Implementation

Immutable, time-locked recovery points for agent state.
"""

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import sqlite3
import logging

logger = logging.getLogger(__name__)

# Optional crypto support
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@dataclass
class SnapshotConfig:
    """Configuration for snapshot storage."""
    
    storage_path: Path = Path("./snapshots")
    db_path: Optional[Path] = None  # Defaults to storage_path/safemode.db
    default_retention_days: int = 30
    max_retention_days: int = 365
    
    # Signing key (optional but recommended)
    signing_key_path: Optional[Path] = None
    
    def __post_init__(self):
        self.storage_path = Path(self.storage_path)
        if self.db_path is None:
            self.db_path = self.storage_path / "safemode.db"
        else:
            self.db_path = Path(self.db_path)


@dataclass
class SafeModeSnapshot:
    """
    An immutable recovery point.
    
    Once created with a retention period, cannot be deleted until
    the retention period expires.
    """
    
    id: str                      # Unique snapshot ID
    hash: str                    # SHA256 of contents
    created_at: datetime         # When snapshot was created
    retention_until: datetime    # Cannot delete before this time
    
    # Metadata
    source_path: str             # Original path that was snapshotted
    size_bytes: int              # Size of snapshot archive
    file_count: int              # Number of files in snapshot
    
    # Provenance
    gateway_id: str              # Gateway that created snapshot
    agent_id: Optional[str] = None
    trace_id: Optional[str] = None
    
    # Signature (if signing key available)
    signature: Optional[bytes] = None
    signed_by: Optional[str] = None  # Key ID
    
    # State
    recovered: bool = False
    recovered_at: Optional[datetime] = None
    
    def is_locked(self) -> bool:
        """Check if snapshot is still in retention period."""
        return datetime.now(timezone.utc) < self.retention_until
    
    def can_delete(self) -> bool:
        """Check if snapshot can be deleted."""
        return not self.is_locked()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "hash": self.hash,
            "created_at": self.created_at.isoformat(),
            "retention_until": self.retention_until.isoformat(),
            "source_path": self.source_path,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "gateway_id": self.gateway_id,
            "agent_id": self.agent_id,
            "trace_id": self.trace_id,
            "is_locked": self.is_locked(),
            "recovered": self.recovered,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SafeModeSnapshot":
        return cls(
            id=data["id"],
            hash=data["hash"],
            created_at=datetime.fromisoformat(data["created_at"]),
            retention_until=datetime.fromisoformat(data["retention_until"]),
            source_path=data["source_path"],
            size_bytes=data["size_bytes"],
            file_count=data["file_count"],
            gateway_id=data["gateway_id"],
            agent_id=data.get("agent_id"),
            trace_id=data.get("trace_id"),
            signature=bytes.fromhex(data["signature"]) if data.get("signature") else None,
            signed_by=data.get("signed_by"),
            recovered=data.get("recovered", False),
            recovered_at=datetime.fromisoformat(data["recovered_at"]) if data.get("recovered_at") else None,
        )


class SnapshotManager:
    """
    Manages SafeMode snapshots with retention enforcement.
    """
    
    def __init__(self, config: SnapshotConfig):
        self.config = config
        self._signing_key = None
        self._signing_key_id = None
        
        # Ensure storage exists
        self.config.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_db()
        
        # Load signing key if available
        if config.signing_key_path and HAS_CRYPTO:
            self._load_signing_key()
    
    def _init_db(self):
        """Initialize SQLite database for snapshot metadata."""
        conn = sqlite3.connect(str(self.config.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                retention_until TEXT NOT NULL,
                source_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                gateway_id TEXT NOT NULL,
                agent_id TEXT,
                trace_id TEXT,
                signature TEXT,
                signed_by TEXT,
                recovered INTEGER DEFAULT 0,
                recovered_at TEXT,
                archive_path TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_retention ON snapshots(retention_until)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent ON snapshots(agent_id)
        """)
        conn.commit()
        conn.close()
    
    def _load_signing_key(self):
        """Load ED25519 signing key."""
        try:
            key_path = self.config.signing_key_path
            if key_path.is_dir():
                key_path = key_path / "private.key"
            
            with open(key_path, "rb") as f:
                key_bytes = f.read()
            
            self._signing_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
            
            # Generate key ID from public key
            pub_bytes = self._signing_key.public_key().public_bytes_raw()
            self._signing_key_id = "sm_" + hashlib.sha256(pub_bytes).hexdigest()[:16]
            
            logger.info(f"SafeMode signing key loaded: {self._signing_key_id}")
        except Exception as e:
            logger.warning(f"Failed to load signing key: {e}")
    
    def create(
        self,
        source_path: Path,
        retention_days: Optional[int] = None,
        gateway_id: str = "unknown",
        agent_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> SafeModeSnapshot:
        """
        Create a new SafeMode snapshot.
        
        Args:
            source_path: Directory to snapshot
            retention_days: How long to lock the snapshot
            gateway_id: ID of gateway creating snapshot
            agent_id: Optional agent ID
            trace_id: Optional OTEL trace ID
        
        Returns:
            The created snapshot
        """
        source_path = Path(source_path)
        if not source_path.exists():
            raise ValueError(f"Source path does not exist: {source_path}")
        
        retention_days = retention_days or self.config.default_retention_days
        retention_days = min(retention_days, self.config.max_retention_days)
        
        now = datetime.now(timezone.utc)
        retention_until = now + timedelta(days=retention_days)
        
        # Generate snapshot ID
        snapshot_id = f"sm_{now.strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(str(source_path).encode()).hexdigest()[:8]}"
        
        # Create archive
        archive_path = self.config.storage_path / f"{snapshot_id}.tar.gz"
        content_hash, size_bytes, file_count = self._create_archive(source_path, archive_path)
        
        # Create snapshot object
        snapshot = SafeModeSnapshot(
            id=snapshot_id,
            hash=content_hash,
            created_at=now,
            retention_until=retention_until,
            source_path=str(source_path),
            size_bytes=size_bytes,
            file_count=file_count,
            gateway_id=gateway_id,
            agent_id=agent_id,
            trace_id=trace_id,
        )
        
        # Sign if key available
        if self._signing_key:
            snapshot.signature = self._sign_snapshot(snapshot)
            snapshot.signed_by = self._signing_key_id
        
        # Store metadata
        self._store_snapshot(snapshot, archive_path)
        
        logger.info(f"SafeMode snapshot created: {snapshot_id} (locked until {retention_until})")
        return snapshot
    
    def _create_archive(self, source_path: Path, archive_path: Path) -> tuple:
        """Create tar.gz archive and return (hash, size, file_count)."""
        hasher = hashlib.sha256()
        file_count = 0
        
        with tarfile.open(archive_path, "w:gz") as tar:
            for root, dirs, files in os.walk(source_path):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(source_path)
                    tar.add(file_path, arcname=arcname)
                    
                    # Update hash
                    with open(file_path, "rb") as f:
                        hasher.update(f.read())
                    file_count += 1
        
        size_bytes = archive_path.stat().st_size
        content_hash = hasher.hexdigest()
        
        return content_hash, size_bytes, file_count
    
    def _sign_snapshot(self, snapshot: SafeModeSnapshot) -> bytes:
        """Sign snapshot metadata."""
        sign_data = f"{snapshot.id}:{snapshot.hash}:{snapshot.retention_until.isoformat()}"
        return self._signing_key.sign(sign_data.encode())
    
    def _store_snapshot(self, snapshot: SafeModeSnapshot, archive_path: Path):
        """Store snapshot metadata in database."""
        conn = sqlite3.connect(str(self.config.db_path))
        conn.execute("""
            INSERT INTO snapshots (
                id, hash, created_at, retention_until, source_path,
                size_bytes, file_count, gateway_id, agent_id, trace_id,
                signature, signed_by, archive_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.id,
            snapshot.hash,
            snapshot.created_at.isoformat(),
            snapshot.retention_until.isoformat(),
            snapshot.source_path,
            snapshot.size_bytes,
            snapshot.file_count,
            snapshot.gateway_id,
            snapshot.agent_id,
            snapshot.trace_id,
            snapshot.signature.hex() if snapshot.signature else None,
            snapshot.signed_by,
            str(archive_path),
        ))
        conn.commit()
        conn.close()
    
    def get(self, snapshot_id: str) -> Optional[SafeModeSnapshot]:
        """Get snapshot by ID."""
        conn = sqlite3.connect(str(self.config.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return SafeModeSnapshot(
            id=row["id"],
            hash=row["hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            retention_until=datetime.fromisoformat(row["retention_until"]),
            source_path=row["source_path"],
            size_bytes=row["size_bytes"],
            file_count=row["file_count"],
            gateway_id=row["gateway_id"],
            agent_id=row["agent_id"],
            trace_id=row["trace_id"],
            signature=bytes.fromhex(row["signature"]) if row["signature"] else None,
            signed_by=row["signed_by"],
            recovered=bool(row["recovered"]),
            recovered_at=datetime.fromisoformat(row["recovered_at"]) if row["recovered_at"] else None,
        )
    
    def list(
        self,
        agent_id: Optional[str] = None,
        include_expired: bool = False,
    ) -> List[SafeModeSnapshot]:
        """List snapshots with optional filtering."""
        conn = sqlite3.connect(str(self.config.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM snapshots WHERE 1=1"
        params = []
        
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        
        if not include_expired:
            query += " AND retention_until > ?"
            params.append(datetime.now(timezone.utc).isoformat())
        
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [
            SafeModeSnapshot(
                id=row["id"],
                hash=row["hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
                retention_until=datetime.fromisoformat(row["retention_until"]),
                source_path=row["source_path"],
                size_bytes=row["size_bytes"],
                file_count=row["file_count"],
                gateway_id=row["gateway_id"],
                agent_id=row["agent_id"],
                trace_id=row["trace_id"],
                signature=bytes.fromhex(row["signature"]) if row["signature"] else None,
                signed_by=row["signed_by"],
                recovered=bool(row["recovered"]),
            )
            for row in rows
        ]
    
    def delete(self, snapshot_id: str, force: bool = False) -> bool:
        """
        Delete a snapshot.
        
        Returns False if snapshot is locked and force=False.
        """
        snapshot = self.get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        
        if snapshot.is_locked() and not force:
            logger.warning(f"Cannot delete locked snapshot: {snapshot_id}")
            return False
        
        # Delete archive
        archive_path = self.config.storage_path / f"{snapshot_id}.tar.gz"
        if archive_path.exists():
            archive_path.unlink()
        
        # Delete from database
        conn = sqlite3.connect(str(self.config.db_path))
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        conn.commit()
        conn.close()
        
        logger.info(f"Snapshot deleted: {snapshot_id}")
        return True
    
    def recover(
        self,
        snapshot_id: str,
        target_path: Path,
        verify: bool = True,
    ) -> bool:
        """
        Recover from a snapshot.
        
        Args:
            snapshot_id: Snapshot to recover from
            target_path: Where to extract files
            verify: Verify hash after extraction
        
        Returns:
            True if recovery succeeded
        """
        snapshot = self.get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        
        archive_path = self.config.storage_path / f"{snapshot_id}.tar.gz"
        if not archive_path.exists():
            raise ValueError(f"Snapshot archive not found: {archive_path}")
        
        target_path = Path(target_path)
        target_path.mkdir(parents=True, exist_ok=True)
        
        # Extract
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(target_path)
        
        # Verify hash if requested
        if verify:
            hasher = hashlib.sha256()
            for root, dirs, files in os.walk(target_path):
                for file in sorted(files):
                    file_path = Path(root) / file
                    with open(file_path, "rb") as f:
                        hasher.update(f.read())
            
            if hasher.hexdigest() != snapshot.hash:
                logger.error(f"Hash mismatch after recovery: {snapshot_id}")
                return False
        
        # Mark as recovered
        conn = sqlite3.connect(str(self.config.db_path))
        conn.execute("""
            UPDATE snapshots SET recovered = 1, recovered_at = ? WHERE id = ?
        """, (datetime.now(timezone.utc).isoformat(), snapshot_id))
        conn.commit()
        conn.close()
        
        logger.info(f"Recovered from snapshot: {snapshot_id} → {target_path}")
        return True
    
    def verify(self, snapshot_id: str) -> Dict[str, Any]:
        """
        Verify snapshot integrity.
        
        Returns verification result with details.
        """
        snapshot = self.get(snapshot_id)
        if not snapshot:
            return {"valid": False, "error": "Snapshot not found"}
        
        archive_path = self.config.storage_path / f"{snapshot_id}.tar.gz"
        if not archive_path.exists():
            return {"valid": False, "error": "Archive file missing"}
        
        result = {
            "snapshot_id": snapshot_id,
            "valid": True,
            "checks": {},
        }
        
        # Check archive exists
        result["checks"]["archive_exists"] = archive_path.exists()
        
        # Check archive size
        actual_size = archive_path.stat().st_size
        result["checks"]["size_match"] = actual_size == snapshot.size_bytes
        
        # Verify signature if present
        if snapshot.signature and self._signing_key:
            try:
                sign_data = f"{snapshot.id}:{snapshot.hash}:{snapshot.retention_until.isoformat()}"
                pub_key = self._signing_key.public_key()
                pub_key.verify(snapshot.signature, sign_data.encode())
                result["checks"]["signature_valid"] = True
            except Exception:
                result["checks"]["signature_valid"] = False
                result["valid"] = False
        
        # Check retention status
        result["is_locked"] = snapshot.is_locked()
        result["retention_until"] = snapshot.retention_until.isoformat()
        
        return result


# === Convenience Functions ===

_default_manager: Optional[SnapshotManager] = None


def get_manager(config: Optional[SnapshotConfig] = None) -> SnapshotManager:
    """Get or create the default snapshot manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SnapshotManager(config or SnapshotConfig())
    return _default_manager


def create_snapshot(
    source_path: Path,
    retention_days: int = 30,
    **kwargs
) -> SafeModeSnapshot:
    """Create a SafeMode snapshot."""
    return get_manager().create(source_path, retention_days, **kwargs)


def verify_snapshot(snapshot_id: str) -> Dict[str, Any]:
    """Verify a snapshot's integrity."""
    return get_manager().verify(snapshot_id)


def list_snapshots(**kwargs) -> List[SafeModeSnapshot]:
    """List available snapshots."""
    return get_manager().list(**kwargs)


def recover_from_snapshot(snapshot_id: str, target_path: Path, **kwargs) -> bool:
    """Recover from a snapshot."""
    return get_manager().recover(snapshot_id, target_path, **kwargs)
