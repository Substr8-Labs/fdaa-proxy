"""
RunProof Bundle - Creation and Loading

A RunProof bundle is a directory (or tarball) containing:
- run.json          - Run header (identity, summary)
- agent/            - FDAA manifest
- policy/           - ACC policy
- ledger/           - DCT entries + verification
- cia/              - CIA receipts
- memory/           - GAM pointers
- meta/             - Build info, tool versions
- RUNPROOF.sha256   - Root hash
- SIGNATURE         - Optional signature
"""

import json
import tarfile
import tempfile
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hash import canonical_json, sha256_str, compute_root_hash


@dataclass
class RunProofBundle:
    """Represents a RunProof bundle in memory."""
    
    run_id: str
    agent_ref: str
    agent_hash: str
    policy_hash: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    
    # Runtime info
    runtime_engine: str = "substr8"
    runtime_gateway: Optional[str] = None
    mcp_endpoint: Optional[str] = None
    
    # Model info
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    
    # Policy
    policy: Optional[Dict[str, Any]] = None
    
    # DCT ledger entries
    ledger_entries: List[Dict[str, Any]] = field(default_factory=list)
    ledger_valid: bool = True
    ledger_head_hash: Optional[str] = None
    
    # CIA receipts
    cia_receipts: List[Dict[str, Any]] = field(default_factory=list)
    cia_repairs: List[Dict[str, Any]] = field(default_factory=list)
    
    # GAM pointers
    gam_pointers: List[Dict[str, Any]] = field(default_factory=list)
    
    # FDAA manifest
    fdaa_manifest: Optional[Dict[str, Any]] = None
    
    # Root hash (computed on save)
    root_hash: Optional[str] = None
    
    def summary(self) -> Dict[str, Any]:
        """Generate summary stats for run.json."""
        return {
            "policy_checks": sum(1 for e in self.ledger_entries if e.get("type") == "policy_check"),
            "tool_calls": sum(1 for e in self.ledger_entries if e.get("type") == "tool_call"),
            "memory_writes": sum(1 for p in self.gam_pointers if p.get("op") == "memory_write"),
            "memory_reads": sum(1 for p in self.gam_pointers if p.get("op") == "memory_read"),
            "cia_receipts": len(self.cia_receipts),
            "chain_valid": self.ledger_valid,
        }
    
    def to_run_json(self) -> Dict[str, Any]:
        """Generate the run.json header."""
        return {
            "run_id": self.run_id,
            "agent_ref": self.agent_ref,
            "agent_hash": self.agent_hash,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "runtime": {
                "engine": self.runtime_engine,
                "gateway": self.runtime_gateway,
                "mcp_endpoint": self.mcp_endpoint,
            },
            "model": {
                "provider": self.model_provider,
                "name": self.model_name,
            },
            "summary": self.summary(),
        }
    
    def save(self, output_dir: Path, create_tarball: bool = True) -> Path:
        """
        Save the RunProof bundle to disk.
        
        Args:
            output_dir: Directory to create the runproof in
            create_tarball: Also create a .runproof.tgz archive
            
        Returns:
            Path to the created directory (or tarball if create_tarball=True)
        """
        bundle_dir = output_dir / f"{self.run_id}"
        runproof_dir = bundle_dir / "runproof"
        
        # Create directory structure
        runproof_dir.mkdir(parents=True, exist_ok=True)
        (runproof_dir / "agent").mkdir(exist_ok=True)
        (runproof_dir / "policy").mkdir(exist_ok=True)
        (runproof_dir / "ledger").mkdir(exist_ok=True)
        (runproof_dir / "cia").mkdir(exist_ok=True)
        (runproof_dir / "memory").mkdir(exist_ok=True)
        (runproof_dir / "meta").mkdir(exist_ok=True)
        
        # Write run.json
        with open(runproof_dir / "run.json", 'w') as f:
            json.dump(self.to_run_json(), f, indent=2)
        
        # Write FDAA manifest
        if self.fdaa_manifest:
            with open(runproof_dir / "agent" / "fdaa.manifest.json", 'w') as f:
                json.dump(self.fdaa_manifest, f, indent=2)
        
        # Write policy
        if self.policy:
            with open(runproof_dir / "policy" / "acc.policy.json", 'w') as f:
                json.dump(self.policy, f, indent=2)
            
            # Write policy hash
            with open(runproof_dir / "policy" / "acc.policy.sha256", 'w') as f:
                f.write(self.policy_hash)
        
        # Write DCT ledger (JSONL)
        with open(runproof_dir / "ledger" / "dct.ledger.jsonl", 'w') as f:
            for entry in self.ledger_entries:
                f.write(canonical_json(entry) + '\n')
        
        # Write DCT verification
        dct_verify = {
            "chain_valid": self.ledger_valid,
            "head_hash": self.ledger_head_hash,
            "entry_count": len(self.ledger_entries),
        }
        with open(runproof_dir / "ledger" / "dct.verify.json", 'w') as f:
            json.dump(dct_verify, f, indent=2)
        
        # Write CIA receipts (JSONL)
        with open(runproof_dir / "cia" / "cia.receipts.jsonl", 'w') as f:
            for receipt in self.cia_receipts:
                f.write(canonical_json(receipt) + '\n')
        
        # Write CIA report
        cia_report = {
            "enabled": True,
            "mode": "audit-only",
            "total_receipts": len(self.cia_receipts),
            "repairs": len(self.cia_repairs),
        }
        with open(runproof_dir / "cia" / "cia.report.json", 'w') as f:
            json.dump(cia_report, f, indent=2)
        
        # Write GAM pointers (JSONL)
        with open(runproof_dir / "memory" / "gam.pointers.jsonl", 'w') as f:
            for pointer in self.gam_pointers:
                f.write(canonical_json(pointer) + '\n')
        
        # Write meta info
        import platform
        build_info = {
            "substr8_version": "1.2.0",
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(runproof_dir / "meta" / "build.json", 'w') as f:
            json.dump(build_info, f, indent=2)
        
        # Compute and write root hash
        root_hash, manifest = compute_root_hash(runproof_dir)
        self.root_hash = root_hash
        
        runproof_sha256 = {
            "run_id": self.run_id,
            "root_hash": root_hash,
            "algorithm": "sha256",
            "file_count": len(manifest),
        }
        with open(runproof_dir / "RUNPROOF.sha256", 'w') as f:
            json.dump(runproof_sha256, f, indent=2)
        
        # Create tarball if requested
        if create_tarball:
            tarball_path = output_dir / f"{self.run_id}.runproof.tgz"
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(runproof_dir, arcname="runproof")
            return tarball_path
        
        return runproof_dir


def create_runproof(
    run_id: str,
    agent_ref: str,
    agent_hash: str,
    policy_hash: str,
    started_at: datetime,
    **kwargs
) -> RunProofBundle:
    """
    Create a new RunProof bundle.
    
    This is the main factory function for creating RunProof bundles.
    """
    return RunProofBundle(
        run_id=run_id,
        agent_ref=agent_ref,
        agent_hash=agent_hash,
        policy_hash=policy_hash,
        started_at=started_at,
        **kwargs
    )


def load_runproof(path: Path) -> RunProofBundle:
    """
    Load a RunProof bundle from disk.
    
    Args:
        path: Path to either a directory or .runproof.tgz file
        
    Returns:
        RunProofBundle instance
    """
    # Handle tarball
    if path.suffix == '.tgz' or path.name.endswith('.runproof.tgz'):
        temp_dir = tempfile.mkdtemp()
        with tarfile.open(path, 'r:gz') as tar:
            tar.extractall(temp_dir)
        runproof_dir = Path(temp_dir) / "runproof"
    else:
        runproof_dir = path
        if (path / "runproof").exists():
            runproof_dir = path / "runproof"
    
    # Load run.json
    with open(runproof_dir / "run.json") as f:
        run_data = json.load(f)
    
    # Load ledger
    ledger_entries = []
    ledger_path = runproof_dir / "ledger" / "dct.ledger.jsonl"
    if ledger_path.exists():
        with open(ledger_path) as f:
            for line in f:
                if line.strip():
                    ledger_entries.append(json.loads(line))
    
    # Load DCT verification
    ledger_valid = True
    ledger_head_hash = None
    dct_verify_path = runproof_dir / "ledger" / "dct.verify.json"
    if dct_verify_path.exists():
        with open(dct_verify_path) as f:
            dct_verify = json.load(f)
            ledger_valid = dct_verify.get("chain_valid", True)
            ledger_head_hash = dct_verify.get("head_hash")
    
    # Load CIA receipts
    cia_receipts = []
    cia_path = runproof_dir / "cia" / "cia.receipts.jsonl"
    if cia_path.exists():
        with open(cia_path) as f:
            for line in f:
                if line.strip():
                    cia_receipts.append(json.loads(line))
    
    # Load GAM pointers
    gam_pointers = []
    gam_path = runproof_dir / "memory" / "gam.pointers.jsonl"
    if gam_path.exists():
        with open(gam_path) as f:
            for line in f:
                if line.strip():
                    gam_pointers.append(json.loads(line))
    
    # Load policy
    policy = None
    policy_hash = run_data.get("agent_hash", "")  # fallback
    policy_path = runproof_dir / "policy" / "acc.policy.json"
    if policy_path.exists():
        with open(policy_path) as f:
            policy = json.load(f)
    
    policy_hash_path = runproof_dir / "policy" / "acc.policy.sha256"
    if policy_hash_path.exists():
        with open(policy_hash_path) as f:
            policy_hash = f.read().strip()
    
    # Load FDAA manifest
    fdaa_manifest = None
    fdaa_path = runproof_dir / "agent" / "fdaa.manifest.json"
    if fdaa_path.exists():
        with open(fdaa_path) as f:
            fdaa_manifest = json.load(f)
    
    # Load root hash
    root_hash = None
    root_hash_path = runproof_dir / "RUNPROOF.sha256"
    if root_hash_path.exists():
        with open(root_hash_path) as f:
            root_hash_data = json.load(f)
            root_hash = root_hash_data.get("root_hash")
    
    # Parse dates
    started_at = datetime.fromisoformat(run_data["started_at"]) if run_data.get("started_at") else datetime.now(timezone.utc)
    ended_at = datetime.fromisoformat(run_data["ended_at"]) if run_data.get("ended_at") else None
    
    return RunProofBundle(
        run_id=run_data["run_id"],
        agent_ref=run_data["agent_ref"],
        agent_hash=run_data["agent_hash"],
        policy_hash=policy_hash,
        started_at=started_at,
        ended_at=ended_at,
        runtime_engine=run_data.get("runtime", {}).get("engine", "substr8"),
        runtime_gateway=run_data.get("runtime", {}).get("gateway"),
        mcp_endpoint=run_data.get("runtime", {}).get("mcp_endpoint"),
        model_provider=run_data.get("model", {}).get("provider"),
        model_name=run_data.get("model", {}).get("name"),
        policy=policy,
        ledger_entries=ledger_entries,
        ledger_valid=ledger_valid,
        ledger_head_hash=ledger_head_hash,
        cia_receipts=cia_receipts,
        gam_pointers=gam_pointers,
        fdaa_manifest=fdaa_manifest,
        root_hash=root_hash,
    )
