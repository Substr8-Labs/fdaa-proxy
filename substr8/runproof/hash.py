"""
Hashing utilities for RunProof canonicalization.

Ensures deterministic hashing across all RunProof files.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def canonical_json(obj: Any) -> str:
    """
    Convert an object to canonical JSON format.
    
    Rules:
    - UTF-8 encoding
    - Sorted keys (recursive)
    - No insignificant whitespace
    - Stable number formatting
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )


def sha256_bytes(data: bytes) -> str:
    """Compute SHA256 hash of bytes, return as hex string."""
    return hashlib.sha256(data).hexdigest()


def sha256_str(data: str) -> str:
    """Compute SHA256 hash of a string (UTF-8 encoded)."""
    return sha256_bytes(data.encode('utf-8'))


def sha256_file(path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    with open(path, 'rb') as f:
        return sha256_bytes(f.read())


def compute_file_manifest(root_dir: Path, exclude: List[str] = None) -> List[Dict[str, str]]:
    """
    Compute a manifest of all files in a directory.
    
    Returns a list of {path, sha256} entries, sorted by path.
    Excludes RUNPROOF.sha256 and SIGNATURE by default.
    """
    exclude = exclude or ["RUNPROOF.sha256", "SIGNATURE"]
    
    manifest = []
    
    for file_path in sorted(root_dir.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(root_dir)
            
            # Skip excluded files
            if str(rel_path) in exclude or rel_path.name in exclude:
                continue
            
            manifest.append({
                "path": str(rel_path),
                "sha256": sha256_file(file_path),
            })
    
    return manifest


def compute_root_hash(root_dir: Path) -> Tuple[str, List[Dict[str, str]]]:
    """
    Compute the root hash over all files in a RunProof bundle.
    
    Algorithm:
    1. Enumerate all files except RUNPROOF.sha256 and SIGNATURE
    2. Compute sha256 per file content
    3. Build a manifest list {path, sha256}
    4. sha256 the canonical JSON of that manifest
    
    Returns:
        Tuple of (root_hash, file_manifest)
    """
    manifest = compute_file_manifest(root_dir)
    
    # Hash the canonical JSON of the manifest
    manifest_json = canonical_json(manifest)
    root_hash = sha256_str(manifest_json)
    
    return root_hash, manifest


def verify_root_hash(root_dir: Path, expected_hash: str) -> Tuple[bool, str, List[Dict[str, str]]]:
    """
    Verify that a RunProof bundle's root hash matches.
    
    Returns:
        Tuple of (valid, actual_hash, manifest)
    """
    actual_hash, manifest = compute_root_hash(root_dir)
    return actual_hash == expected_hash, actual_hash, manifest
