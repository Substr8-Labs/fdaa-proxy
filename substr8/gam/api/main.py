"""
GAM API Service - Multi-tenant Memory API

FastAPI service for Git-Native Agent Memory operations.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..db.client import GAMDatabaseClient


# === Models ===

class TenantCreate(BaseModel):
    name: str
    git_repo_url: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class TenantResponse(BaseModel):
    id: str
    name: str
    git_repo_url: Optional[str]
    created_at: datetime
    settings: dict


class BranchCreate(BaseModel):
    name: str
    level: str  # main, c-suite, project, feature
    parent_branch: Optional[str] = None
    description: Optional[str] = None


class BranchResponse(BaseModel):
    id: str
    name: str
    level: str
    parent_branch: Optional[str]
    description: Optional[str]
    is_active: bool
    created_at: datetime


class MemoryStore(BaseModel):
    memory_id: str
    file_path: str
    content: str
    section: Optional[str] = None
    source: str = "user"
    confidence: str = "medium"
    classification: str = "private"
    tags: list[str] = Field(default_factory=list)
    commit_sha: Optional[str] = None
    author: Optional[str] = None
    embedding: Optional[list[float]] = None


class MemoryResponse(BaseModel):
    id: str
    memory_id: str
    file_path: str
    section: Optional[str]
    content: str
    classification: str
    tags: list[str]
    created_at: datetime


class SearchRequest(BaseModel):
    query_embedding: list[float]
    limit: int = 10
    threshold: float = 0.3
    classification: Optional[str] = None


class SearchResult(BaseModel):
    memory_id: str
    file_path: str
    section: Optional[str]
    content: str
    similarity: float
    tags: list[str]
    classification: str


class ProposalCreate(BaseModel):
    proposal_type: str  # extract, remember, forget
    target_branch: str
    title: str
    source_branch: Optional[str] = None
    description: Optional[str] = None
    entries: list[dict] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)


class ProposalResponse(BaseModel):
    id: str
    proposal_id: str
    type: str
    source_branch: Optional[str]
    target_branch: str
    title: str
    status: str
    created_at: datetime


class ProposalStatusUpdate(BaseModel):
    status: str  # pending, approved, rejected, merged
    reviewed_by: Optional[str] = None
    review_notes: Optional[str] = None
    commit_sha: Optional[str] = None


class AuditEvent(BaseModel):
    event_type: str
    actor: str
    branch: Optional[str] = None
    memory_id: Optional[str] = None
    proposal_id: Optional[str] = None
    details: dict = Field(default_factory=dict)
    trace_id: Optional[str] = None


# === Database Dependency ===

_db_client: Optional[GAMDatabaseClient] = None


def get_db() -> GAMDatabaseClient:
    """Get database client."""
    global _db_client
    if _db_client is None:
        _db_client = GAMDatabaseClient()
    return _db_client


def get_tenant_id(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> str:
    """Extract tenant ID from header."""
    return x_tenant_id


# === App ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global _db_client
    _db_client = GAMDatabaseClient()
    yield
    if _db_client:
        _db_client.close()


app = FastAPI(
    title="GAM API",
    description="Git-Native Agent Memory - Multi-tenant Memory Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Health ===

@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "service": "gam-api"}


@app.get("/ready")
async def ready(db: GAMDatabaseClient = Depends(get_db)):
    """Readiness check (includes DB)."""
    try:
        # Quick DB check
        db.list_tenants()
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database not ready: {e}")


# === Tenants ===

@app.post("/v1/tenants", response_model=TenantResponse)
async def create_tenant(
    tenant: TenantCreate,
    db: GAMDatabaseClient = Depends(get_db),
):
    """Create a new tenant."""
    result = db.create_tenant(
        name=tenant.name,
        git_repo_url=tenant.git_repo_url,
        settings=tenant.settings,
    )
    return TenantResponse(**result)


@app.get("/v1/tenants", response_model=list[TenantResponse])
async def list_tenants(db: GAMDatabaseClient = Depends(get_db)):
    """List all tenants."""
    results = db.list_tenants()
    return [TenantResponse(**r) for r in results]


@app.get("/v1/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    db: GAMDatabaseClient = Depends(get_db),
):
    """Get a tenant by ID."""
    result = db.get_tenant(tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse(**result)


# === Branches ===

@app.post("/v1/branches", response_model=BranchResponse)
async def create_branch(
    branch: BranchCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Create a branch."""
    result = db.create_branch(
        tenant_id=tenant_id,
        name=branch.name,
        level=branch.level,
        parent_branch=branch.parent_branch,
        description=branch.description,
    )
    return BranchResponse(**result)


@app.get("/v1/branches", response_model=list[BranchResponse])
async def list_branches(
    level: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """List branches for tenant."""
    results = db.list_branches(tenant_id=tenant_id, level=level)
    return [BranchResponse(**r) for r in results]


@app.get("/v1/branches/{name}", response_model=BranchResponse)
async def get_branch(
    name: str,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Get a branch by name."""
    result = db.get_branch(tenant_id=tenant_id, name=name)
    if not result:
        raise HTTPException(status_code=404, detail="Branch not found")
    return BranchResponse(**result)


# === Memories ===

@app.post("/v1/memories/{branch}", response_model=MemoryResponse)
async def store_memory(
    branch: str,
    memory: MemoryStore,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Store a memory entry."""
    import hashlib
    content_hash = hashlib.sha256(memory.content.encode()).hexdigest()
    
    result = db.store_memory(
        tenant_id=tenant_id,
        branch=branch,
        memory_id=memory.memory_id,
        file_path=memory.file_path,
        content=memory.content,
        content_hash=content_hash,
        section=memory.section,
        source=memory.source,
        confidence=memory.confidence,
        classification=memory.classification,
        tags=memory.tags,
        commit_sha=memory.commit_sha,
        author=memory.author,
        embedding=memory.embedding,
    )
    
    # Log audit event
    db.log_event(
        tenant_id=tenant_id,
        event_type="remember",
        actor=memory.author or "api",
        branch=branch,
        memory_id=memory.memory_id,
    )
    
    return MemoryResponse(**result)


@app.post("/v1/memories/{branch}/search", response_model=list[SearchResult])
async def search_memories(
    branch: str,
    search: SearchRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Semantic search for memories."""
    results = db.search_memories(
        tenant_id=tenant_id,
        branch=branch,
        query_embedding=search.query_embedding,
        limit=search.limit,
        threshold=search.threshold,
        classification=search.classification,
    )
    return [SearchResult(**r) for r in results]


@app.get("/v1/memories/{branch}/by-tags", response_model=list[MemoryResponse])
async def get_memories_by_tags(
    branch: str,
    tags: str = Query(..., description="Comma-separated tags"),
    classification: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Get memories matching tags."""
    tag_list = [t.strip() for t in tags.split(",")]
    results = db.get_memories_by_tags(
        tenant_id=tenant_id,
        branch=branch,
        tags=tag_list,
        classification=classification,
    )
    return [MemoryResponse(**r) for r in results]


# === Proposals ===

@app.post("/v1/proposals", response_model=ProposalResponse)
async def create_proposal(
    proposal: ProposalCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Create a memory proposal."""
    import hashlib
    from datetime import datetime
    
    # Generate proposal ID
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_suffix = hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:6]
    proposal_id = f"prop_{timestamp}_{random_suffix}"
    
    result = db.create_proposal(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        proposal_type=proposal.proposal_type,
        target_branch=proposal.target_branch,
        title=proposal.title,
        source_branch=proposal.source_branch,
        description=proposal.description,
        entries=proposal.entries,
        filters=proposal.filters,
    )
    
    # Log audit
    db.log_event(
        tenant_id=tenant_id,
        event_type="propose",
        actor="api",
        proposal_id=proposal_id,
    )
    
    return ProposalResponse(
        id=result["id"],
        proposal_id=result["proposal_id"],
        type=result["type"],
        source_branch=result.get("source_branch"),
        target_branch=result["target_branch"],
        title=result["title"],
        status=result["status"],
        created_at=result["created_at"],
    )


@app.get("/v1/proposals", response_model=list[ProposalResponse])
async def list_proposals(
    status: Optional[str] = Query(None),
    target_branch: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """List proposals."""
    results = db.list_proposals(
        tenant_id=tenant_id,
        status=status,
        target_branch=target_branch,
    )
    return [
        ProposalResponse(
            id=r["id"],
            proposal_id=r["proposal_id"],
            type=r["type"],
            source_branch=r.get("source_branch"),
            target_branch=r["target_branch"],
            title=r["title"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in results
    ]


@app.patch("/v1/proposals/{proposal_id}/status", response_model=ProposalResponse)
async def update_proposal_status(
    proposal_id: str,
    update: ProposalStatusUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Update proposal status."""
    result = db.update_proposal_status(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        status=update.status,
        reviewed_by=update.reviewed_by,
        review_notes=update.review_notes,
        commit_sha=update.commit_sha,
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Proposal not found")
    
    # Log audit
    db.log_event(
        tenant_id=tenant_id,
        event_type=update.status,  # approve, reject, merge
        actor=update.reviewed_by or "api",
        proposal_id=proposal_id,
    )
    
    return ProposalResponse(
        id=result["id"],
        proposal_id=result["proposal_id"],
        type=result["type"],
        source_branch=result.get("source_branch"),
        target_branch=result["target_branch"],
        title=result["title"],
        status=result["status"],
        created_at=result["created_at"],
    )


# === Audit ===

@app.post("/v1/audit")
async def log_audit_event(
    event: AuditEvent,
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Log an audit event."""
    result = db.log_event(
        tenant_id=tenant_id,
        event_type=event.event_type,
        actor=event.actor,
        branch=event.branch,
        memory_id=event.memory_id,
        proposal_id=event.proposal_id,
        details=event.details,
        trace_id=event.trace_id,
    )
    return {"id": result["id"], "created_at": result["created_at"]}


@app.get("/v1/audit")
async def get_audit_log(
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    tenant_id: str = Depends(get_tenant_id),
    db: GAMDatabaseClient = Depends(get_db),
):
    """Get audit log entries."""
    results = db.get_audit_log(
        tenant_id=tenant_id,
        event_type=event_type,
        limit=limit,
    )
    return results


# === Run ===

def run_server(host: str = "0.0.0.0", port: int = 8091):
    """Run the API server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
