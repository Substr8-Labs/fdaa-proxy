"""
GAM Sparse Checkout - Performance Optimization for Large Repos

Implements:
- Git sparse checkout for loading only memory directories
- Partial clone support
- Index pruning by date/topic/entity
"""

import subprocess
from pathlib import Path
from typing import Optional


def enable_sparse_checkout(repo_path: Path, patterns: Optional[list[str]] = None) -> bool:
    """
    Enable sparse checkout for a repository.
    
    Only loads specified patterns, reducing I/O for large repos.
    
    Args:
        repo_path: Path to git repository
        patterns: List of patterns to include (default: memory-related)
    
    Returns:
        True if successful
    """
    if patterns is None:
        patterns = [
            "MEMORY.md",
            "memory/",
            ".gam/",
            "AGENTS.md",
            "SOUL.md",
            "USER.md",
        ]
    
    try:
        # Enable sparse checkout
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "core.sparseCheckout", "true"],
            check=True,
            capture_output=True,
        )
        
        # Write sparse-checkout patterns
        sparse_file = repo_path / ".git" / "info" / "sparse-checkout"
        sparse_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(sparse_file, "w") as f:
            for pattern in patterns:
                f.write(f"{pattern}\n")
        
        # Apply sparse checkout
        subprocess.run(
            ["git", "-C", str(repo_path), "read-tree", "-mu", "HEAD"],
            check=True,
            capture_output=True,
        )
        
        return True
    except subprocess.CalledProcessError:
        return False


def disable_sparse_checkout(repo_path: Path) -> bool:
    """
    Disable sparse checkout and restore full tree.
    """
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "core.sparseCheckout", "false"],
            check=True,
            capture_output=True,
        )
        
        subprocess.run(
            ["git", "-C", str(repo_path), "read-tree", "-mu", "HEAD"],
            check=True,
            capture_output=True,
        )
        
        return True
    except subprocess.CalledProcessError:
        return False


def setup_partial_clone(remote_url: str, local_path: Path, filter_spec: str = "blob:none") -> bool:
    """
    Clone a repository with partial clone (blobless).
    
    This downloads only commit/tree objects initially,
    fetching blobs on demand.
    
    Args:
        remote_url: Git remote URL
        local_path: Local destination path
        filter_spec: Filter specification (default: blob:none for blobless)
    
    Returns:
        True if successful
    """
    try:
        subprocess.run(
            [
                "git", "clone",
                "--filter", filter_spec,
                "--sparse",
                remote_url,
                str(local_path),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def add_sparse_pattern(repo_path: Path, pattern: str) -> bool:
    """Add a pattern to sparse checkout."""
    sparse_file = repo_path / ".git" / "info" / "sparse-checkout"
    
    if not sparse_file.exists():
        enable_sparse_checkout(repo_path, [pattern])
        return True
    
    # Read existing patterns
    with open(sparse_file) as f:
        patterns = f.read().splitlines()
    
    if pattern not in patterns:
        patterns.append(pattern)
        
        with open(sparse_file, "w") as f:
            for p in patterns:
                f.write(f"{p}\n")
        
        # Apply
        subprocess.run(
            ["git", "-C", str(repo_path), "read-tree", "-mu", "HEAD"],
            capture_output=True,
        )
    
    return True


def remove_sparse_pattern(repo_path: Path, pattern: str) -> bool:
    """Remove a pattern from sparse checkout."""
    sparse_file = repo_path / ".git" / "info" / "sparse-checkout"
    
    if not sparse_file.exists():
        return False
    
    with open(sparse_file) as f:
        patterns = f.read().splitlines()
    
    if pattern in patterns:
        patterns.remove(pattern)
        
        with open(sparse_file, "w") as f:
            for p in patterns:
                f.write(f"{p}\n")
        
        subprocess.run(
            ["git", "-C", str(repo_path), "read-tree", "-mu", "HEAD"],
            capture_output=True,
        )
    
    return True


def get_sparse_patterns(repo_path: Path) -> list[str]:
    """Get current sparse checkout patterns."""
    sparse_file = repo_path / ".git" / "info" / "sparse-checkout"
    
    if not sparse_file.exists():
        return []
    
    with open(sparse_file) as f:
        return f.read().splitlines()


def is_sparse_enabled(repo_path: Path) -> bool:
    """Check if sparse checkout is enabled."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "config", "core.sparseCheckout"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip().lower() == "true"
    except subprocess.CalledProcessError:
        return False


# === Index Pruning ===

def prune_index_by_date(
    repo_path: Path,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> int:
    """
    Prune temporal index to only include memories in date range.
    
    Args:
        repo_path: Repository path
        before: ISO date string (memories before this date)
        after: ISO date string (memories after this date)
    
    Returns:
        Number of memories remaining after pruning
    """
    from .index import TemporalIndex
    import sqlite3
    from datetime import datetime
    
    gam_dir = repo_path / ".gam"
    db_path = gam_dir / "index.sqlite"
    
    if not db_path.exists():
        return 0
    
    conn = sqlite3.connect(db_path)
    
    conditions = []
    params = []
    
    if before:
        before_ts = int(datetime.fromisoformat(before).timestamp())
        conditions.append("created_at < ?")
        params.append(before_ts)
    
    if after:
        after_ts = int(datetime.fromisoformat(after).timestamp())
        conditions.append("created_at > ?")
        params.append(after_ts)
    
    if conditions:
        where_clause = " AND ".join(conditions)
        conn.execute(f"DELETE FROM memories WHERE NOT ({where_clause})", params)
        conn.commit()
    
    cursor = conn.execute("SELECT COUNT(*) FROM memories")
    count = cursor.fetchone()[0]
    conn.close()
    
    return count
