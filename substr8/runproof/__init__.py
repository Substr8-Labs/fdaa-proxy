"""
RunProof - Portable, Verifiable Agent Run Artifacts

RunProof is a cryptographically verifiable artifact produced at the end
of every governed agent run. It packages:

- Who ran (agent identity + hash)
- What they were allowed to do (ACC policy + policy hash)
- What they did (DCT tamper-evident ledger)
- Conversation integrity receipts (CIA)
- Memory provenance pointers (GAM)
"""

from .bundle import RunProofBundle, create_runproof, load_runproof
from .verify import verify_runproof, VerificationResult
from .hash import compute_root_hash, canonical_json

__all__ = [
    "RunProofBundle",
    "create_runproof",
    "load_runproof",
    "verify_runproof",
    "VerificationResult",
    "compute_root_hash",
    "canonical_json",
]
