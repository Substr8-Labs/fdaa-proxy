"""
GAM HTTP API

Simple REST API for Git-Native Agent Memory.
Enables trace-aware memory commits from external services.
"""

import os
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Header
from pydantic import BaseModel

from .core import GAMRepository, MemoryMetadata, open_gam, init_gam


# === Configuration ===

GAM_REPO_PATH = os.getenv("GAM_REPO_PATH", ".")


# === Models ===

class RememberRequest(BaseModel):
    """Request to store a memory."""
    content: str
    title: Optional[str] = None
    source: str = "api"
    confidence: str = "medium"
    classification: str = "private"
    tags: list[str] = []
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


class MemoryResponse(BaseModel):
    """Response with memory details."""
    id: str
    file_path: str
    commit_sha: str
    created_at: str
    trace_id: Optional[str] = None


class RecallRequest(BaseModel):
    """Request to search memories."""
    query: str
    limit: int = 10
    semantic: bool = False


class RecallResult(BaseModel):
    """A search result."""
    id: str
    file_path: str
    content: str
    score: float
    source: str


# === API ===

app = FastAPI(
    title="GAM API",
    description="Git-Native Agent Memory HTTP API",
    version="0.1.0",
)


def get_repo() -> GAMRepository:
    """Get or initialize GAM repository."""
    path = Path(GAM_REPO_PATH)
    
    if (path / ".gam").exists():
        return open_gam(path)
    elif (path / ".git").exists():
        return init_gam(path)
    else:
        raise HTTPException(
            status_code=500,
            detail=f"No GAM repository at {path}"
        )


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "repo_path": GAM_REPO_PATH}


@app.post("/remember", response_model=MemoryResponse)
async def remember(
    request: RememberRequest,
    x_trace_id: Optional[str] = Header(None, alias="X-Trace-ID"),
    x_span_id: Optional[str] = Header(None, alias="X-Span-ID"),
):
    """
    Store a new memory.
    
    Trace context can be provided via:
    - Request body (trace_id, span_id)
    - Headers (X-Trace-ID, X-Span-ID)
    """
    repo = get_repo()
    
    # Prefer body, fall back to headers
    trace_id = request.trace_id or x_trace_id
    span_id = request.span_id or x_span_id
    
    trace_context = None
    if trace_id:
        trace_context = {"trace_id": trace_id}
        if span_id:
            trace_context["span_id"] = span_id
    
    metadata = MemoryMetadata(
        source=request.source,
        confidence=request.confidence,
        classification=request.classification,
        tags=request.tags,
    )
    
    try:
        memory = repo.remember(
            content=request.content,
            title=request.title,
            metadata=metadata,
            trace_context=trace_context,
            require_signature=False,  # API doesn't have signing context
        )
        
        return MemoryResponse(
            id=memory.id,
            file_path=memory.file_path,
            commit_sha=memory.commit_sha,
            created_at=memory.created_at.isoformat(),
            trace_id=trace_id,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recall", response_model=list[RecallResult])
async def recall(request: RecallRequest):
    """Search memories."""
    repo = get_repo()
    
    try:
        if request.semantic:
            memories = repo.recall_semantic(
                query=request.query,
                limit=request.limit,
            )
        else:
            memories = repo.recall(
                query=request.query,
                limit=request.limit,
            )
        
        return [
            RecallResult(
                id=m.id,
                file_path=m.file_path,
                content=m.content[:500] + "..." if len(m.content) > 500 else m.content,
                score=getattr(m, 'score', 1.0),
                source=m.metadata.source,
            )
            for m in memories
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/verify/{memory_id}")
async def verify(memory_id: str):
    """Verify memory provenance."""
    repo = get_repo()
    
    try:
        result = repo.verify(memory_id)
        return {
            "id": memory_id,
            "valid": result.valid,
            "commit_sha": result.commit_sha,
            "signature_valid": result.signature_valid,
            "chain_valid": result.chain_valid,
            "errors": result.errors,
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


def run_server(host: str = "0.0.0.0", port: int = 8090):
    """Run the GAM API server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
