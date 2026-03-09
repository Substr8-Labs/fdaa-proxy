"""
GAM Index - Semantic and Temporal Indexing

Provides:
- Embedding-based semantic search
- Temporal decay scoring
- Access tracking for reinforcement
"""

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Optional imports for semantic search
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False


@dataclass
class ScoredMemory:
    """Memory with relevance score."""
    memory_id: str
    file_path: str
    content: str
    score: float
    semantic_score: float = 0.0
    decay_score: float = 1.0
    reinforcement_bonus: float = 1.0


class TemporalIndex:
    """
    SQLite-based temporal index for memory metadata and access tracking.
    """
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                modified_at INTEGER NOT NULL,
                source TEXT,
                confidence TEXT,
                classification TEXT,
                decay_exempt INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                accessed_at INTEGER NOT NULL,
                query TEXT,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            );
            
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_classification ON memories(classification);
            CREATE INDEX IF NOT EXISTS idx_access_memory ON access_log(memory_id);
            CREATE INDEX IF NOT EXISTS idx_access_time ON access_log(accessed_at);
        """)
        conn.commit()
        conn.close()
    
    def index_memory(
        self,
        memory_id: str,
        file_path: str,
        content: str,
        source: str = "unknown",
        confidence: str = "medium",
        classification: str = "private",
        decay_exempt: bool = False,
    ):
        """Add or update a memory in the index."""
        now = int(time.time())
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO memories 
            (id, file_path, content_hash, created_at, modified_at, source, confidence, classification, decay_exempt)
            VALUES (?, ?, ?, COALESCE((SELECT created_at FROM memories WHERE id = ?), ?), ?, ?, ?, ?, ?)
        """, (memory_id, file_path, content_hash, memory_id, now, now, source, confidence, classification, int(decay_exempt)))
        conn.commit()
        conn.close()
    
    def log_access(self, memory_id: str, query: str = ""):
        """Log a memory access for reinforcement."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO access_log (memory_id, accessed_at, query) VALUES (?, ?, ?)",
            (memory_id, int(time.time()), query)
        )
        conn.commit()
        conn.close()
    
    def get_decay_score(self, memory_id: str, lambda_decay: float = 0.01) -> float:
        """
        Calculate decay score for a memory.
        
        score = e^(-λ * days_since_creation)
        Default λ = 0.01 gives half-life of ~69 days
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT created_at, decay_exempt FROM memories WHERE id = ?",
            (memory_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return 1.0
        
        created_at, decay_exempt = row
        
        if decay_exempt:
            return 1.0
        
        days_since = (time.time() - created_at) / 86400
        return math.exp(-lambda_decay * days_since)
    
    def get_reinforcement_bonus(self, memory_id: str, window_days: int = 30) -> float:
        """
        Calculate reinforcement bonus based on recent accesses.
        
        bonus = 1 + (0.1 * access_count_in_window)
        """
        cutoff = int(time.time()) - (window_days * 86400)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM access_log WHERE memory_id = ? AND accessed_at > ?",
            (memory_id, cutoff)
        )
        count = cursor.fetchone()[0]
        conn.close()
        
        return 1.0 + (0.1 * count)
    
    def get_combined_score(
        self,
        memory_id: str,
        base_relevance: float = 1.0,
        lambda_decay: float = 0.01,
    ) -> tuple[float, float, float]:
        """
        Calculate combined temporal score.
        
        Returns: (total_score, decay_score, reinforcement_bonus)
        """
        decay = self.get_decay_score(memory_id, lambda_decay)
        reinforcement = self.get_reinforcement_bonus(memory_id)
        
        total = base_relevance * decay * reinforcement
        return total, decay, reinforcement


class SemanticIndex:
    """
    Embedding-based semantic search index.
    
    Requires sentence-transformers: pip install gam[retrieval]
    """
    
    def __init__(
        self,
        index_dir: Path,
        model_name: str = "all-MiniLM-L6-v2",
    ):
        if not HAS_EMBEDDINGS:
            raise ImportError(
                "Semantic search requires sentence-transformers. "
                "Install with: pip install gam[retrieval]"
            )
        
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        
        self.model = SentenceTransformer(model_name)
        self.embeddings_file = index_dir / "embeddings.npy"
        self.ids_file = index_dir / "ids.json"
        
        self._load_index()
    
    def _load_index(self):
        """Load existing index or initialize empty."""
        if self.embeddings_file.exists() and self.ids_file.exists():
            self.embeddings = np.load(self.embeddings_file)
            with open(self.ids_file) as f:
                self.memory_ids = json.load(f)
        else:
            self.embeddings = np.array([]).reshape(0, self.model.get_sentence_embedding_dimension())
            self.memory_ids = []
    
    def _save_index(self):
        """Persist index to disk."""
        np.save(self.embeddings_file, self.embeddings)
        with open(self.ids_file, "w") as f:
            json.dump(self.memory_ids, f)
    
    def index_memory(self, memory_id: str, content: str):
        """Add or update a memory's embedding."""
        embedding = self.model.encode([content])[0]
        
        if memory_id in self.memory_ids:
            # Update existing
            idx = self.memory_ids.index(memory_id)
            self.embeddings[idx] = embedding
        else:
            # Add new
            self.memory_ids.append(memory_id)
            self.embeddings = np.vstack([self.embeddings, embedding]) if len(self.embeddings) > 0 else np.array([embedding])
        
        self._save_index()
    
    def remove_memory(self, memory_id: str):
        """Remove a memory from the index."""
        if memory_id not in self.memory_ids:
            return
        
        idx = self.memory_ids.index(memory_id)
        self.memory_ids.pop(idx)
        self.embeddings = np.delete(self.embeddings, idx, axis=0)
        self._save_index()
    
    def search(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.3,
    ) -> list[tuple[str, float]]:
        """
        Search for memories by semantic similarity.
        
        Returns: List of (memory_id, similarity_score) tuples
        """
        if len(self.embeddings) == 0:
            return []
        
        query_embedding = self.model.encode([query])[0]
        
        # Cosine similarity
        similarities = np.dot(self.embeddings, query_embedding) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        )
        
        # Get top results above threshold
        indices = np.argsort(similarities)[::-1]
        results = []
        
        for idx in indices[:limit]:
            score = float(similarities[idx])
            if score >= threshold:
                results.append((self.memory_ids[idx], score))
        
        return results
    
    def rebuild_from_files(self, memory_files: list[tuple[str, str]]):
        """
        Rebuild entire index from memory files.
        
        Args:
            memory_files: List of (memory_id, content) tuples
        """
        if not memory_files:
            self.embeddings = np.array([]).reshape(0, self.model.get_sentence_embedding_dimension())
            self.memory_ids = []
            self._save_index()
            return
        
        ids, contents = zip(*memory_files)
        self.memory_ids = list(ids)
        self.embeddings = self.model.encode(list(contents))
        self._save_index()


class GAMIndex:
    """
    Combined index manager for GAM repositories.
    
    Provides unified interface to temporal and semantic indexing.
    """
    
    def __init__(self, gam_dir: Path, enable_semantic: bool = True):
        self.gam_dir = gam_dir
        
        # Always have temporal index
        self.temporal = TemporalIndex(gam_dir / "index.sqlite")
        
        # Semantic index is optional
        self.semantic: Optional[SemanticIndex] = None
        if enable_semantic and HAS_EMBEDDINGS:
            try:
                self.semantic = SemanticIndex(gam_dir / "embeddings")
            except Exception as e:
                print(f"Warning: Could not initialize semantic index: {e}")
    
    def index_memory(
        self,
        memory_id: str,
        file_path: str,
        content: str,
        source: str = "unknown",
        confidence: str = "medium",
        classification: str = "private",
        decay_exempt: bool = False,
    ):
        """Index a memory in all indices."""
        self.temporal.index_memory(
            memory_id, file_path, content,
            source, confidence, classification, decay_exempt
        )
        
        if self.semantic:
            self.semantic.index_memory(memory_id, content)
    
    def search(
        self,
        query: str,
        limit: int = 10,
        use_semantic: bool = True,
    ) -> list[tuple[str, float]]:
        """
        Search memories with combined scoring.
        
        Returns: List of (memory_id, combined_score) tuples
        """
        results = {}
        
        # Semantic search if available
        if use_semantic and self.semantic:
            for memory_id, sem_score in self.semantic.search(query, limit=limit * 2):
                total, decay, reinf = self.temporal.get_combined_score(memory_id, sem_score)
                results[memory_id] = total
        
        # Sort and limit
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:limit]
    
    def log_access(self, memory_id: str, query: str = ""):
        """Log memory access."""
        self.temporal.log_access(memory_id, query)
    
    def rebuild_semantic(self, memory_files: list[tuple[str, str]]):
        """Rebuild semantic index from scratch."""
        if self.semantic:
            self.semantic.rebuild_from_files(memory_files)
