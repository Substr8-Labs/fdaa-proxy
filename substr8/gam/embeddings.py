"""
GAM Embeddings - Multiple embedding provider support

Provides:
- OpenAI embeddings (text-embedding-3-small)
- Local embeddings (sentence-transformers)
- ChromaDB vector store (optional)
"""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Optional imports
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False


@dataclass
class SearchResult:
    """A search result with metadata."""
    memory_id: str
    content: str
    score: float
    file_path: Optional[str] = None
    metadata: Optional[dict] = None


class EmbeddingProvider(ABC):
    """Base class for embedding providers."""
    
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts."""
        pass
    
    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return embedding dimension."""
        pass


class OpenAIEmbeddings(EmbeddingProvider):
    """OpenAI text-embedding-3-small embeddings."""
    
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
    ):
        if not HAS_OPENAI:
            raise ImportError("openai package required. Install with: pip install openai")
        
        self.model = model
        self.client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self._dimension = 1536  # text-embedding-3-small
    
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts."""
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [r.embedding for r in response.data]
    
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        return self.embed([text])[0]
    
    @property
    def dimension(self) -> int:
        return self._dimension


class LocalEmbeddings(EmbeddingProvider):
    """Local sentence-transformers embeddings."""
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError(
                "sentence-transformers required. Install with: pip install sentence-transformers"
            )
        
        self.model = SentenceTransformer(model_name)
        self._dimension = self.model.get_sentence_embedding_dimension()
    
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts."""
        return self.model.encode(texts).tolist()
    
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        return self.model.encode([text])[0].tolist()
    
    @property
    def dimension(self) -> int:
        return self._dimension


class VectorStore(ABC):
    """Base class for vector stores."""
    
    @abstractmethod
    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        contents: list[str],
        metadatas: Optional[list[dict]] = None,
    ):
        """Add vectors to the store."""
        pass
    
    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        """Search for similar vectors."""
        pass
    
    @abstractmethod
    def delete(self, ids: list[str]):
        """Delete vectors by ID."""
        pass
    
    @abstractmethod
    def count(self) -> int:
        """Return number of vectors in store."""
        pass


class ChromaVectorStore(VectorStore):
    """ChromaDB vector store - persistent, local, fast."""
    
    def __init__(
        self,
        path: Path,
        collection_name: str = "gam_memories",
    ):
        if not HAS_CHROMADB:
            raise ImportError("chromadb required. Install with: pip install chromadb")
        
        self.client = chromadb.PersistentClient(
            path=str(path),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    
    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        contents: list[str],
        metadatas: Optional[list[dict]] = None,
    ):
        """Add vectors to the store."""
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=contents,
            metadatas=metadatas or [{}] * len(ids),
        )
    
    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        """Search for similar vectors."""
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=filter,
            include=["documents", "metadatas", "distances"],
        )
        
        search_results = []
        for i, id_ in enumerate(results["ids"][0]):
            # ChromaDB returns distances, convert to similarity
            distance = results["distances"][0][i] if results["distances"] else 0
            score = 1 - distance  # cosine distance to similarity
            
            search_results.append(SearchResult(
                memory_id=id_,
                content=results["documents"][0][i] if results["documents"] else "",
                score=score,
                metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                file_path=results["metadatas"][0][i].get("file_path") if results["metadatas"] else None,
            ))
        
        return search_results
    
    def delete(self, ids: list[str]):
        """Delete vectors by ID."""
        self.collection.delete(ids=ids)
    
    def count(self) -> int:
        """Return number of vectors in store."""
        return self.collection.count()


class NumpyVectorStore(VectorStore):
    """Simple numpy-based vector store - no dependencies."""
    
    def __init__(self, path: Path):
        if not HAS_NUMPY:
            raise ImportError("numpy required. Install with: pip install numpy")
        
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        
        self.embeddings_file = path / "embeddings.npy"
        self.metadata_file = path / "metadata.json"
        
        self._load()
    
    def _load(self):
        """Load index from disk."""
        if self.embeddings_file.exists() and self.metadata_file.exists():
            self.embeddings = np.load(self.embeddings_file)
            with open(self.metadata_file) as f:
                self.metadata = json.load(f)
        else:
            self.embeddings = None
            self.metadata = {"ids": [], "contents": [], "metas": []}
    
    def _save(self):
        """Save index to disk."""
        if self.embeddings is not None:
            np.save(self.embeddings_file, self.embeddings)
        with open(self.metadata_file, "w") as f:
            json.dump(self.metadata, f)
    
    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        contents: list[str],
        metadatas: Optional[list[dict]] = None,
    ):
        """Add vectors to the store."""
        metadatas = metadatas or [{}] * len(ids)
        new_embeddings = np.array(embeddings)
        
        for i, id_ in enumerate(ids):
            if id_ in self.metadata["ids"]:
                # Update existing
                idx = self.metadata["ids"].index(id_)
                self.embeddings[idx] = new_embeddings[i]
                self.metadata["contents"][idx] = contents[i]
                self.metadata["metas"][idx] = metadatas[i]
            else:
                # Add new
                self.metadata["ids"].append(id_)
                self.metadata["contents"].append(contents[i])
                self.metadata["metas"].append(metadatas[i])
                
                if self.embeddings is None:
                    self.embeddings = new_embeddings[i:i+1]
                else:
                    self.embeddings = np.vstack([self.embeddings, new_embeddings[i]])
        
        self._save()
    
    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        """Search for similar vectors."""
        if self.embeddings is None or len(self.embeddings) == 0:
            return []
        
        query = np.array(query_embedding)
        
        # Cosine similarity
        similarities = np.dot(self.embeddings, query) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query)
        )
        
        # Get top results
        indices = np.argsort(similarities)[::-1][:limit]
        
        results = []
        for idx in indices:
            meta = self.metadata["metas"][idx]
            
            # Apply filter if provided
            if filter:
                skip = False
                for k, v in filter.items():
                    if meta.get(k) != v:
                        skip = True
                        break
                if skip:
                    continue
            
            results.append(SearchResult(
                memory_id=self.metadata["ids"][idx],
                content=self.metadata["contents"][idx],
                score=float(similarities[idx]),
                metadata=meta,
                file_path=meta.get("file_path"),
            ))
        
        return results
    
    def delete(self, ids: list[str]):
        """Delete vectors by ID."""
        for id_ in ids:
            if id_ in self.metadata["ids"]:
                idx = self.metadata["ids"].index(id_)
                self.metadata["ids"].pop(idx)
                self.metadata["contents"].pop(idx)
                self.metadata["metas"].pop(idx)
                self.embeddings = np.delete(self.embeddings, idx, axis=0)
        
        self._save()
    
    def count(self) -> int:
        """Return number of vectors in store."""
        return len(self.metadata["ids"])


class GAMSemanticSearch:
    """
    High-level semantic search for GAM repositories.
    
    Combines embedding provider + vector store for full semantic search.
    """
    
    def __init__(
        self,
        gam_dir: Path,
        provider: str = "auto",  # "openai", "local", "auto"
        store: str = "auto",     # "chroma", "numpy", "auto"
    ):
        self.gam_dir = gam_dir
        self.search_dir = gam_dir / "search"
        self.search_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize embedding provider
        self.embedder = self._init_embedder(provider)
        
        # Initialize vector store
        self.store = self._init_store(store)
    
    def _init_embedder(self, provider: str) -> EmbeddingProvider:
        """Initialize embedding provider."""
        if provider == "openai" or (provider == "auto" and HAS_OPENAI and os.environ.get("OPENAI_API_KEY")):
            return OpenAIEmbeddings()
        elif provider == "local" or (provider == "auto" and HAS_SENTENCE_TRANSFORMERS):
            return LocalEmbeddings()
        else:
            raise ImportError(
                "No embedding provider available. Install:\n"
                "  pip install openai  (and set OPENAI_API_KEY)\n"
                "  OR pip install sentence-transformers"
            )
    
    def _init_store(self, store: str) -> VectorStore:
        """Initialize vector store."""
        if store == "chroma" or (store == "auto" and HAS_CHROMADB):
            return ChromaVectorStore(self.search_dir / "chroma")
        elif store == "numpy" or (store == "auto" and HAS_NUMPY):
            return NumpyVectorStore(self.search_dir / "numpy")
        else:
            raise ImportError(
                "No vector store available. Install:\n"
                "  pip install chromadb  (recommended)\n"
                "  OR pip install numpy"
            )
    
    def index_memory(
        self,
        memory_id: str,
        content: str,
        file_path: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """Index a single memory."""
        embedding = self.embedder.embed_query(content)
        
        meta = metadata or {}
        if file_path:
            meta["file_path"] = file_path
        
        self.store.add(
            ids=[memory_id],
            embeddings=[embedding],
            contents=[content],
            metadatas=[meta],
        )
    
    def index_batch(
        self,
        memories: list[tuple[str, str, Optional[str], Optional[dict]]],
        batch_size: int = 100,
        progress_callback: Optional[callable] = None,
    ) -> int:
        """
        Index multiple memories in batches.
        
        Args:
            memories: List of (memory_id, content, file_path, metadata) tuples
            batch_size: Number of memories to embed at once
            progress_callback: Optional callback(indexed, total)
        
        Returns:
            Number of memories indexed
        """
        total = len(memories)
        indexed = 0
        
        for i in range(0, total, batch_size):
            batch = memories[i:i + batch_size]
            
            ids = [m[0] for m in batch]
            contents = [m[1] for m in batch]
            file_paths = [m[2] for m in batch]
            metadatas = []
            
            for j, m in enumerate(batch):
                meta = m[3] or {}
                if file_paths[j]:
                    meta["file_path"] = file_paths[j]
                metadatas.append(meta)
            
            # Embed batch
            embeddings = self.embedder.embed(contents)
            
            # Store
            self.store.add(
                ids=ids,
                embeddings=embeddings,
                contents=contents,
                metadatas=metadatas,
            )
            
            indexed += len(batch)
            
            if progress_callback:
                progress_callback(indexed, total)
        
        return indexed
    
    def search(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.3,
        filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        """
        Search for memories semantically.
        
        Args:
            query: Search query
            limit: Maximum results
            threshold: Minimum similarity score (0-1)
            filter: Optional metadata filter
        
        Returns:
            List of SearchResult objects
        """
        query_embedding = self.embedder.embed_query(query)
        results = self.store.search(query_embedding, limit=limit * 2, filter=filter)
        
        # Filter by threshold
        filtered = [r for r in results if r.score >= threshold]
        return filtered[:limit]
    
    def delete(self, memory_id: str):
        """Delete a memory from the index."""
        self.store.delete([memory_id])
    
    def count(self) -> int:
        """Return number of indexed memories."""
        return self.store.count()
    
    def status(self) -> dict:
        """Return index status."""
        embedder_type = type(self.embedder).__name__
        store_type = type(self.store).__name__
        
        return {
            "embedder": embedder_type,
            "store": store_type,
            "count": self.count(),
            "path": str(self.search_dir),
        }
