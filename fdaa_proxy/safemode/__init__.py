"""
SafeMode Snapshots

Immutable recovery points for agent state that cannot be deleted
even by privileged administrators for a set retention period.

Features:
- Content-addressed snapshots (SHA256)
- Retention locks (time-based)
- Signed by gateway (ED25519)
- Recovery verification
"""

from .snapshot import (
    SafeModeSnapshot,
    SnapshotManager,
    SnapshotConfig,
    create_snapshot,
    verify_snapshot,
    list_snapshots,
    recover_from_snapshot,
)

__all__ = [
    "SafeModeSnapshot",
    "SnapshotManager",
    "SnapshotConfig",
    "create_snapshot",
    "verify_snapshot",
    "list_snapshots",
    "recover_from_snapshot",
]
