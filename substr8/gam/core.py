"""
GAM Core - Git-Native Agent Memory

Core operations: remember, recall, verify, forget
"""

import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

import git
import yaml

from .index import GAMIndex, HAS_EMBEDDINGS
from .permissions import PermissionManager, PermissionLevel
from .identity import IdentityManager, HAS_CRYPTO


# === Data Types ===

SourceType = Literal["conversation", "observation", "user", "inferred", "import"]
ConfidenceLevel = Literal["high", "medium", "low"]
ClassificationType = Literal["private", "shared", "public"]


@dataclass
class MemoryMetadata:
    """Metadata for a memory entry."""
    source: SourceType = "conversation"
    confidence: ConfidenceLevel = "medium"
    classification: ClassificationType = "private"
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    decay_exempt: bool = False


@dataclass
class Memory:
    """A memory with content and provenance."""
    id: str
    file_path: str
    content: str
    metadata: MemoryMetadata
    created_at: datetime
    modified_at: datetime
    commit_sha: Optional[str] = None
    signature_valid: Optional[bool] = None
    

@dataclass
class VerificationResult:
    """Result of memory verification."""
    valid: bool
    reason: Optional[str] = None
    commit_sha: Optional[str] = None
    signature_valid: Optional[bool] = None
    author: Optional[str] = None
    timestamp: Optional[datetime] = None
    lineage: list[str] = field(default_factory=list)


# === GAM Repository ===

class GAMRepository:
    """Git-Native Agent Memory repository."""
    
    def __init__(self, path: str | Path, enable_semantic: bool = True):
        self.path = Path(path).resolve()
        self.repo = git.Repo(self.path)
        self.gam_dir = self.path / ".gam"
        self.config = self._load_config()
        self._ensure_structure()
        
        # Initialize index
        self.index = GAMIndex(self.gam_dir, enable_semantic=enable_semantic)
        
        # Initialize permissions
        self.permissions = PermissionManager(self.gam_dir)
        
        # Initialize identity (lazy - needs passphrase)
        self._identity_manager: Optional[IdentityManager] = None
        self._active_agent: Optional[str] = None
    
    def _load_config(self) -> dict:
        """Load GAM configuration."""
        config_path = self.gam_dir / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)
        return self._default_config()
    
    def _default_config(self) -> dict:
        """Default GAM configuration."""
        return {
            "version": 1,
            "identity": {
                "name": "Agent",
                "email": "agent@localhost",
                "sign_commits": False,
            },
            "decay": {
                "enabled": True,
                "lambda": 0.01,
            },
        }
    
    def init_identity(self, passphrase: str) -> IdentityManager:
        """Initialize identity manager with master seed."""
        self._identity_manager = IdentityManager(self.gam_dir)
        self._identity_manager.init_master_seed(passphrase)
        return self._identity_manager
    
    def set_active_agent(self, agent_name: str):
        """Set the active agent for signing operations."""
        self._active_agent = agent_name
    
    @property
    def identity(self) -> Optional[IdentityManager]:
        """Get identity manager (None if not initialized)."""
        return self._identity_manager
    
    def _ensure_structure(self):
        """Ensure GAM directory structure exists."""
        dirs = [
            self.gam_dir,
            self.path / "memory" / "daily",
            self.path / "memory" / "topics",
            self.path / "memory" / "entities",
            self.path / "memory" / "archive",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
    
    def _generate_id(self) -> str:
        """Generate a unique memory ID."""
        ts = int(time.time() * 1000)
        rand = hashlib.sha256(os.urandom(8)).hexdigest()[:4]
        return f"mem_{ts}_{rand}"
    
    def _compute_content_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _format_frontmatter(self, memory_id: str, metadata: MemoryMetadata) -> str:
        """Format YAML frontmatter for a memory."""
        now = datetime.now(timezone.utc).isoformat()
        fm = {
            "gam_version": 1,
            "id": memory_id,
            "created": now,
            "modified": now,
            "source": metadata.source,
            "confidence": metadata.confidence,
            "classification": metadata.classification,
        }
        if metadata.tags:
            fm["tags"] = metadata.tags
        if metadata.related:
            fm["related"] = metadata.related
        if metadata.decay_exempt:
            fm["decay_exempt"] = True
        
        return f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
    
    def _parse_memory_file(self, file_path: Path) -> list[Memory]:
        """Parse memories from a file."""
        if not file_path.exists():
            return []
        
        content = file_path.read_text()
        memories = []
        
        # Split by frontmatter blocks
        parts = re.split(r'^---\s*$', content, flags=re.MULTILINE)
        
        i = 0
        while i < len(parts):
            if i + 2 < len(parts):
                # Try to parse as frontmatter + content
                try:
                    fm = yaml.safe_load(parts[i + 1])
                    if fm and isinstance(fm, dict) and "id" in fm:
                        body = parts[i + 2].strip()
                        memories.append(Memory(
                            id=fm["id"],
                            file_path=str(file_path.relative_to(self.path)),
                            content=body,
                            metadata=MemoryMetadata(
                                source=fm.get("source", "unknown"),
                                confidence=fm.get("confidence", "medium"),
                                classification=fm.get("classification", "private"),
                                tags=fm.get("tags", []),
                                related=fm.get("related", []),
                                decay_exempt=fm.get("decay_exempt", False),
                            ),
                            created_at=datetime.fromisoformat(fm["created"]) if "created" in fm else datetime.now(timezone.utc),
                            modified_at=datetime.fromisoformat(fm["modified"]) if "modified" in fm else datetime.now(timezone.utc),
                        ))
                        i += 2
                        continue
                except (yaml.YAMLError, KeyError):
                    pass
            i += 1
        
        return memories
    
    def _route_memory(self, content: str, metadata: MemoryMetadata) -> Path:
        """Determine which file a memory should go in."""
        # Check tags for routing hints
        tags = set(metadata.tags)
        
        # Explicit daily tag goes to daily
        if "daily" in tags:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return self.path / "memory" / "daily" / f"{date_str}.md"
        
        # Entity tag with related paths
        if "entity" in tags and len(metadata.related) > 0:
            entity_name = Path(metadata.related[0]).stem
            return self.path / "memory" / "entities" / f"{entity_name}.md"
        
        # Route by first tag to topics (if not "daily")
        if metadata.tags:
            topic = metadata.tags[0].lower().replace(" ", "-")
            return self.path / "memory" / "topics" / f"{topic}.md"
        
        # Only fallback to daily for conversations with no tags
        if metadata.source == "conversation":
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return self.path / "memory" / "daily" / f"{date_str}.md"
        
        # Final fallback
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.path / "memory" / "daily" / f"{date_str}.md"
    
    # === Core Operations ===
    
    def remember(
        self,
        content: str,
        title: Optional[str] = None,
        metadata: Optional[MemoryMetadata] = None,
        file_path: Optional[Path] = None,
        require_signature: bool = True,
        trace_context: Optional[dict] = None,
    ) -> Memory:
        """
        Store a new memory with full provenance.
        
        Args:
            content: The memory content (Markdown)
            title: Optional title (extracted from content if not provided)
            metadata: Memory metadata (defaults provided if not specified)
            file_path: Explicit file path (auto-routed if not specified)
            require_signature: Enforce permission-based signing (default True)
            trace_context: Optional OTEL trace context {"trace_id": ..., "span_id": ...}
                          Links memory commit to the request that triggered it.
        
        Returns:
            The created Memory with commit SHA
        
        Raises:
            PermissionError: If path requires signature and none available
        """
        metadata = metadata or MemoryMetadata()
        memory_id = self._generate_id()
        
        # Route to appropriate file
        target = file_path or self._route_memory(content, metadata)
        rel_path = str(target.relative_to(self.path)) if target.is_absolute() else str(target)
        
        # Check permissions
        if require_signature:
            perm = self.permissions.config.get_permission(rel_path)
            
            if perm == PermissionLevel.READONLY:
                raise PermissionError(f"Path '{rel_path}' is read-only")
            
            if perm == PermissionLevel.HUMAN_SIGN:
                raise PermissionError(
                    f"Path '{rel_path}' requires human GPG signature. "
                    "Use git commit -S manually or set require_signature=False"
                )
            
            if perm == PermissionLevel.AGENT_SIGN:
                if not (self._identity_manager and self._active_agent):
                    raise PermissionError(
                        f"Path '{rel_path}' requires agent signature. "
                        "Call init_identity() and set_active_agent() first, "
                        "or set require_signature=False"
                    )
        
        # Ensure target directory exists
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # Format the entry
        frontmatter = self._format_frontmatter(memory_id, metadata)
        
        # Add title if provided
        if title:
            body = f"# {title}\n\n{content}"
        else:
            body = content
        
        entry = f"{frontmatter}{body}\n\n"
        
        # Append to file
        with open(target, "a") as f:
            f.write(entry)
        
        # Git commit
        rel_path = target.relative_to(self.path)
        self.repo.index.add([str(rel_path)])
        
        # Build commit message
        scope = self._path_to_scope(rel_path)
        brief = (title or content[:50].replace("\n", " "))[:50]
        commit_msg = f"memory({scope}): {brief}\n\n"
        commit_msg += f"- Source: {metadata.source}\n"
        commit_msg += f"- Confidence: {metadata.confidence}\n"
        commit_msg += f"- Classification: {metadata.classification}\n"
        if metadata.related:
            commit_msg += f"- Related: {', '.join(metadata.related)}\n"
        
        # Add OTEL trace context if provided (links memory to request trace)
        if trace_context:
            if trace_context.get("trace_id"):
                commit_msg += f"- Trace-ID: {trace_context['trace_id']}\n"
            if trace_context.get("span_id"):
                commit_msg += f"- Span-ID: {trace_context['span_id']}\n"
        
        # Add agent signature if available and required
        agent_signature = None
        if require_signature and self._identity_manager and self._active_agent:
            agent = self._identity_manager.get_agent(self._active_agent)
            if agent:
                import base64
                # Sign the memory content
                sig_content = f"{memory_id}:{rel_path}:{self._compute_content_hash(body)}"
                agent_signature = agent.sign(sig_content.encode())
                sig_b64 = base64.b64encode(agent_signature).decode()
                commit_msg += f"\nAgent-Signature: {agent.did}\nSignature: {sig_b64[:64]}..."
        
        # Commit (signing handled by git config)
        commit = self.repo.index.commit(commit_msg)
        
        # Update index
        self.index.index_memory(
            memory_id=memory_id,
            file_path=str(rel_path),
            content=body,
            source=metadata.source,
            confidence=metadata.confidence,
            classification=metadata.classification,
            decay_exempt=metadata.decay_exempt,
        )
        
        # Create Memory object
        now = datetime.now(timezone.utc)
        return Memory(
            id=memory_id,
            file_path=str(rel_path),
            content=body,
            metadata=metadata,
            created_at=now,
            modified_at=now,
            commit_sha=commit.hexsha,
        )
    
    def recall(
        self,
        query: str,
        limit: int = 10,
        classification: Optional[ClassificationType] = None,
        include_raw: bool = True,
    ) -> list[Memory]:
        """
        Retrieve memories matching a query.
        
        This is a simple keyword search. For semantic search,
        use recall_semantic() with the retrieval extras installed.
        
        Args:
            query: Search query (keywords)
            limit: Maximum results
            classification: Filter by classification level
            include_raw: Also search files without GAM frontmatter
        
        Returns:
            List of matching memories, scored and sorted
        """
        query_terms = query.lower().split()
        results = []
        
        # Search all memory files
        for pattern in ["MEMORY.md", "memory/**/*.md"]:
            for file_path in self.path.glob(pattern):
                if ".gam" in str(file_path):
                    continue
                
                # Try parsing as GAM format first
                memories = self._parse_memory_file(file_path)
                
                if memories:
                    for mem in memories:
                        # Filter by classification
                        if classification and mem.metadata.classification != classification:
                            continue
                        
                        # Simple keyword matching
                        content_lower = mem.content.lower()
                        score = sum(1 for term in query_terms if term in content_lower)
                        
                        if score > 0:
                            results.append((score, mem))
                elif include_raw:
                    # No GAM frontmatter - search raw content
                    content = file_path.read_text()
                    content_lower = content.lower()
                    score = sum(1 for term in query_terms if term in content_lower)
                    
                    if score > 0:
                        # Create a pseudo-memory from raw file
                        rel_path = str(file_path.relative_to(self.path))
                        mem = Memory(
                            id=f"raw:{rel_path}",
                            file_path=rel_path,
                            content=content[:2000],  # Truncate for display
                            metadata=MemoryMetadata(source="import"),
                            created_at=datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
                            modified_at=datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
                        )
                        results.append((score, mem))
        
        # Sort by score descending
        results.sort(key=lambda x: x[0], reverse=True)
        
        # Log access (for reinforcement)
        self._log_access([m.id for _, m in results[:limit]], query)
        
        return [m for _, m in results[:limit]]
    
    def recall_semantic(
        self,
        query: str,
        limit: int = 10,
        classification: Optional[ClassificationType] = None,
    ) -> list[Memory]:
        """
        Retrieve memories using semantic (embedding) search.
        
        Requires the retrieval extras: pip install gam[retrieval]
        Falls back to keyword search if embeddings unavailable.
        
        Args:
            query: Natural language search query
            limit: Maximum results
            classification: Filter by classification level
        
        Returns:
            List of matching memories with temporal scoring applied
        """
        if not HAS_EMBEDDINGS or not self.index.semantic:
            # Fallback to keyword search
            return self.recall(query, limit, classification)
        
        # Semantic search with temporal scoring
        search_results = self.index.search(query, limit=limit * 2, use_semantic=True)
        
        # Load full memory objects
        memories = []
        memory_map = {}
        
        # Build map of all memories
        for pattern in ["MEMORY.md", "memory/**/*.md"]:
            for file_path in self.path.glob(pattern):
                if ".gam" in str(file_path):
                    continue
                for mem in self._parse_memory_file(file_path):
                    memory_map[mem.id] = mem
        
        # Retrieve matching memories
        for memory_id, score in search_results:
            if memory_id in memory_map:
                mem = memory_map[memory_id]
                
                # Filter by classification
                if classification and mem.metadata.classification != classification:
                    continue
                
                memories.append(mem)
                
                if len(memories) >= limit:
                    break
        
        # Log access for reinforcement
        self.index.log_access(memory_id, query)
        for mem in memories:
            self.index.log_access(mem.id, query)
        
        return memories
    
    def rebuild_index(self):
        """
        Rebuild all indexes from memory files.
        
        Use after bulk imports or to fix index corruption.
        """
        memory_files = []
        
        for pattern in ["MEMORY.md", "memory/**/*.md"]:
            for file_path in self.path.glob(pattern):
                if ".gam" in str(file_path):
                    continue
                
                memories = self._parse_memory_file(file_path)
                for mem in memories:
                    # Index in temporal
                    self.index.temporal.index_memory(
                        memory_id=mem.id,
                        file_path=mem.file_path,
                        content=mem.content,
                        source=mem.metadata.source,
                        confidence=mem.metadata.confidence,
                        classification=mem.metadata.classification,
                        decay_exempt=mem.metadata.decay_exempt,
                    )
                    memory_files.append((mem.id, mem.content))
        
        # Rebuild semantic index
        if self.index.semantic:
            self.index.rebuild_semantic(memory_files)
        
        return len(memory_files)
    
    def verify(self, memory_id: str) -> VerificationResult:
        """
        Verify a memory's provenance and integrity.
        
        Args:
            memory_id: The memory ID to verify
        
        Returns:
            VerificationResult with validity and lineage
        """
        # Find the memory
        memory = None
        for pattern in ["MEMORY.md", "memory/**/*.md"]:
            for file_path in self.path.glob(pattern):
                if ".gam" in str(file_path):
                    continue
                memories = self._parse_memory_file(file_path)
                for m in memories:
                    if m.id == memory_id:
                        memory = m
                        break
                if memory:
                    break
        
        if not memory:
            return VerificationResult(valid=False, reason="memory_not_found")
        
        # Find the commit that introduced this memory
        try:
            rel_path = memory.file_path
            log = list(self.repo.iter_commits(paths=rel_path, max_count=100))
            
            # Search commits for the memory ID
            origin_commit = None
            for commit in log:
                try:
                    diff = commit.diff(commit.parents[0] if commit.parents else git.NULL_TREE)
                    for d in diff:
                        if d.a_path == rel_path or d.b_path == rel_path:
                            # Check if this diff contains our memory ID
                            if d.b_blob and memory_id in d.b_blob.data_stream.read().decode():
                                origin_commit = commit
                                break
                except Exception:
                    continue
                if origin_commit:
                    break
            
            if not origin_commit:
                # Fallback: use oldest commit touching this file
                origin_commit = log[-1] if log else None
            
            if not origin_commit:
                return VerificationResult(valid=False, reason="commit_not_found")
            
            # Build lineage
            lineage = [c.hexsha[:8] for c in log[:10]]
            
            return VerificationResult(
                valid=True,
                commit_sha=origin_commit.hexsha,
                author=str(origin_commit.author),
                timestamp=datetime.fromtimestamp(origin_commit.committed_date, tz=timezone.utc),
                lineage=lineage,
            )
        
        except Exception as e:
            return VerificationResult(valid=False, reason=f"verification_error: {e}")
    
    def forget(
        self,
        memory_id: str,
        reason: str = "User requested deletion",
        hard: bool = False,
    ) -> bool:
        """
        Remove a memory.
        
        Args:
            memory_id: The memory ID to forget
            reason: Reason for deletion (logged)
            hard: If True, rewrite git history (use for PII)
        
        Returns:
            True if successfully forgotten
        """
        if hard:
            raise NotImplementedError("Hard delete (history rewrite) not yet implemented")
        
        # Find and remove the memory
        for pattern in ["MEMORY.md", "memory/**/*.md"]:
            for file_path in self.path.glob(pattern):
                if ".gam" in str(file_path):
                    continue
                
                content = file_path.read_text()
                
                # Find the memory block and replace with tombstone
                if memory_id in content:
                    # Match: ---\n(frontmatter with id)---\n\n(content until next --- or end)
                    # Use .*? with DOTALL to match across lines
                    new_content = re.sub(
                        rf'---\n(?:(?!---\n).)*id: {re.escape(memory_id)}(?:(?!---\n).)*\n---\n\n.*?(?=\n---\n|\Z)',
                        f'<!-- FORGOTTEN: {memory_id} - {reason} -->',
                        content,
                        flags=re.DOTALL
                    )
                    
                    if new_content != content:
                        file_path.write_text(new_content)
                        
                        # Commit
                        rel_path = file_path.relative_to(self.path)
                        self.repo.index.add([str(rel_path)])
                        self.repo.index.commit(f"memory(forget): {memory_id}\n\nReason: {reason}")
                        return True
        
        return False
    
    # === Helpers ===
    
    def _path_to_scope(self, path: Path) -> str:
        """Convert file path to commit scope."""
        path_str = str(path)
        if path_str == "MEMORY.md":
            return "core"
        if "daily" in path_str:
            return "daily"
        if "topics" in path_str:
            topic = path.stem
            return f"topic/{topic}"
        if "entities" in path_str:
            entity = path.stem
            return f"entity/{entity}"
        return "misc"
    
    def _log_access(self, memory_ids: list[str], query: str):
        """Log memory access for reinforcement."""
        log_path = self.gam_dir / "access.jsonl"
        timestamp = int(time.time())
        
        import json
        with open(log_path, "a") as f:
            for mid in memory_ids:
                entry = {"memory_id": mid, "timestamp": timestamp, "query": query}
                f.write(json.dumps(entry) + "\n")


# === Convenience Functions ===

def init_gam(path: str | Path) -> GAMRepository:
    """Initialize a new GAM repository."""
    path = Path(path).resolve()
    
    # Initialize git if needed
    if not (path / ".git").exists():
        git.Repo.init(path)
    
    # Create GAM structure
    repo = GAMRepository(path)
    
    # Write default config
    config_path = repo.gam_dir / "config.yaml"
    if not config_path.exists():
        with open(config_path, "w") as f:
            yaml.dump(repo._default_config(), f)
    
    # Initial commit
    repo.repo.index.add([".gam/config.yaml"])
    try:
        repo.repo.index.commit("gam: initialize repository")
    except Exception:
        pass  # May already be committed
    
    return repo


def open_gam(path: str | Path) -> GAMRepository:
    """Open an existing GAM repository."""
    return GAMRepository(path)
