-- GAM Database Schema for Postgres + pgvector
-- Multi-tenant memory storage with semantic search

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Tenants table (Control Tower users)
CREATE TABLE IF NOT EXISTS gam_tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    git_repo_url VARCHAR(512),  -- Git repo for this tenant
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    settings JSONB DEFAULT '{}'::jsonb
);

-- Branches table (tracks branch hierarchy)
CREATE TABLE IF NOT EXISTS gam_branches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES gam_tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    level VARCHAR(50) NOT NULL,  -- main, c-suite, project, feature
    parent_branch VARCHAR(255),
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    
    UNIQUE(tenant_id, name)
);

CREATE INDEX idx_branches_tenant ON gam_branches(tenant_id);
CREATE INDEX idx_branches_level ON gam_branches(level);

-- Memory entries table (indexed chunks)
CREATE TABLE IF NOT EXISTS gam_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES gam_tenants(id) ON DELETE CASCADE,
    branch VARCHAR(255) NOT NULL,
    
    -- Content
    memory_id VARCHAR(255) NOT NULL,  -- GAM memory ID (file:hash)
    file_path VARCHAR(512) NOT NULL,
    section VARCHAR(255),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,  -- SHA256
    
    -- Metadata
    source VARCHAR(50) DEFAULT 'user',  -- user, conversation, inferred, import
    confidence VARCHAR(20) DEFAULT 'medium',
    classification VARCHAR(50) DEFAULT 'private',
    tags TEXT[] DEFAULT '{}',
    
    -- Git provenance
    commit_sha VARCHAR(40),
    author VARCHAR(255),
    committed_at TIMESTAMPTZ,
    
    -- Embedding (1536 dims for OpenAI, adjust for others)
    embedding vector(1536),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(tenant_id, branch, memory_id)
);

CREATE INDEX idx_memories_tenant ON gam_memories(tenant_id);
CREATE INDEX idx_memories_branch ON gam_memories(tenant_id, branch);
CREATE INDEX idx_memories_classification ON gam_memories(classification);
CREATE INDEX idx_memories_tags ON gam_memories USING GIN(tags);
CREATE INDEX idx_memories_embedding ON gam_memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Proposals table (memory change requests)
CREATE TABLE IF NOT EXISTS gam_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES gam_tenants(id) ON DELETE CASCADE,
    proposal_id VARCHAR(255) NOT NULL,  -- GAM proposal ID
    
    -- Proposal details
    type VARCHAR(50) NOT NULL,  -- extract, remember, forget
    source_branch VARCHAR(255),
    target_branch VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL,
    description TEXT,
    
    -- Status
    status VARCHAR(50) DEFAULT 'draft',  -- draft, pending, approved, rejected, merged
    
    -- Entries (JSON array of memory entries)
    entries JSONB DEFAULT '[]'::jsonb,
    filters JSONB DEFAULT '{}'::jsonb,
    
    -- Review
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_by VARCHAR(255),
    reviewed_at TIMESTAMPTZ,
    review_notes TEXT,
    
    -- Result
    commit_sha VARCHAR(40),
    merged_at TIMESTAMPTZ,
    
    UNIQUE(tenant_id, proposal_id)
);

CREATE INDEX idx_proposals_tenant ON gam_proposals(tenant_id);
CREATE INDEX idx_proposals_status ON gam_proposals(status);
CREATE INDEX idx_proposals_target ON gam_proposals(tenant_id, target_branch);

-- Audit log (memory operations)
CREATE TABLE IF NOT EXISTS gam_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES gam_tenants(id) ON DELETE CASCADE,
    
    -- Event
    event_type VARCHAR(50) NOT NULL,  -- remember, forget, extract, propose, approve, merge
    actor VARCHAR(255),  -- agent DID or human identifier
    
    -- Context
    branch VARCHAR(255),
    memory_id VARCHAR(255),
    proposal_id VARCHAR(255),
    
    -- Details
    details JSONB DEFAULT '{}'::jsonb,
    
    -- Provenance
    trace_id VARCHAR(64),  -- OTEL trace ID
    commit_sha VARCHAR(40),
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_tenant ON gam_audit_log(tenant_id);
CREATE INDEX idx_audit_type ON gam_audit_log(event_type);
CREATE INDEX idx_audit_time ON gam_audit_log(created_at DESC);

-- Row Level Security (multi-tenant isolation)
ALTER TABLE gam_tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE gam_branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE gam_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE gam_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE gam_audit_log ENABLE ROW LEVEL SECURITY;

-- Note: Semantic search is done in application code using:
-- SELECT memory_id, content, 1 - (embedding <=> query_vec) as similarity
-- FROM gam_memories WHERE tenant_id = ? AND branch = ?
-- ORDER BY embedding <=> query_vec LIMIT ?
