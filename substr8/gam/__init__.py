"""
GAM - Git-Native Agent Memory

Verifiable, auditable memory for AI agents using git primitives.
"""

from .core import (
    GAMRepository,
    Memory, 
    MemoryMetadata,
    VerificationResult,
    init_gam,
    open_gam,
)
from .index import (
    GAMIndex,
    TemporalIndex,
    SemanticIndex,
    ScoredMemory,
    HAS_EMBEDDINGS,
)
from .identity import (
    AgentIdentity,
    HumanIdentity,
    IdentityManager,
    create_did_key,
    verify_agent_signature,
    verify_git_commit_signature,
    HAS_CRYPTO,
)
from .sparse import (
    enable_sparse_checkout,
    disable_sparse_checkout,
    setup_partial_clone,
    is_sparse_enabled,
    get_sparse_patterns,
)
from .permissions import (
    PermissionLevel,
    PathPolicy,
    PermissionConfig,
    PermissionManager,
)
from .autocommit import (
    GAMAutoCommit,
    TraceContext,
    observe,
    decision,
    init_autocommit,
    get_autocommit,
)
from .branches import (
    BranchLevel,
    BranchInfo,
    BranchManager,
    parse_branch_level,
    validate_branch_name,
)
from .proposals import (
    ProposalStatus,
    ProposalType,
    MemoryEntry,
    Proposal,
    ProposalManager,
)

# Database client (optional - requires psycopg2)
try:
    from .db.client import GAMDatabaseClient
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False

# Semantic search (optional - requires extras)
try:
    from .embeddings import (
        GAMSemanticSearch,
        OpenAIEmbeddings,
        LocalEmbeddings,
        ChromaVectorStore,
        NumpyVectorStore,
        SearchResult,
    )
    HAS_SEMANTIC = True
except ImportError:
    HAS_SEMANTIC = False

__version__ = "0.1.0"
__all__ = [
    "GAMRepository",
    "Memory", 
    "MemoryMetadata",
    "VerificationResult",
    "init_gam",
    "open_gam",
    "GAMIndex",
    "TemporalIndex",
    "SemanticIndex",
    "ScoredMemory",
    "HAS_EMBEDDINGS",
    "HAS_SEMANTIC",
    # Auto-commit integration
    "GAMAutoCommit",
    "TraceContext",
    "observe",
    "decision",
    "init_autocommit",
    "get_autocommit",
    # Branch management
    "BranchLevel",
    "BranchInfo",
    "BranchManager",
    "parse_branch_level",
    "validate_branch_name",
    # Proposals (PR model)
    "ProposalStatus",
    "ProposalType",
    "MemoryEntry",
    "Proposal",
    "ProposalManager",
    # Semantic search (when available)
    "GAMSemanticSearch",
    "OpenAIEmbeddings",
    "LocalEmbeddings",
    "ChromaVectorStore",
    "NumpyVectorStore",
    "SearchResult",
]
