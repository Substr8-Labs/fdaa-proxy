"""
GAM Memory Proposals - Human-in-the-Loop Memory Changes

Implements the PR model for memory changes:
- Create proposals for memory transfers between branches
- Review what memory entries will be included
- Approve/reject with audit trail
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from .core import GAMRepository


class ProposalStatus(Enum):
    """Status of a memory proposal."""
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"


class ProposalType(Enum):
    """Type of memory proposal."""
    EXTRACT = "extract"       # Extract tagged memories to new branch
    CHERRY_PICK = "cherry-pick"  # Pick specific commits
    REMEMBER = "remember"     # Add new memory (human approval required)
    FORGET = "forget"         # Remove memory (requires approval)


@dataclass
class MemoryEntry:
    """A memory entry to be included in a proposal."""
    file_path: str
    content: str
    section: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    classification: str = "private"
    redacted: bool = False
    redaction_reason: Optional[str] = None
    
    @property
    def id(self) -> str:
        """Generate a unique ID for this entry."""
        content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:12]
        return f"{self.file_path}:{content_hash}"


@dataclass
class Proposal:
    """A memory proposal (like a PR for memory changes)."""
    id: str
    type: ProposalType
    source_branch: str
    target_branch: str
    status: ProposalStatus
    title: str
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "gam"
    entries: list[MemoryEntry] = field(default_factory=list)
    filters: dict = field(default_factory=dict)  # Tag/classification filters used
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: str = ""
    commit_sha: Optional[str] = None  # Resulting commit if merged


class ProposalManager:
    """Manages memory proposals."""
    
    def __init__(self, repo: GAMRepository):
        self.repo = repo
        self.proposals_dir = repo.gam_dir / "proposals"
        self.proposals_dir.mkdir(exist_ok=True)
    
    def _proposal_path(self, proposal_id: str) -> Path:
        """Get the path to a proposal file."""
        return self.proposals_dir / f"{proposal_id}.yaml"
    
    def _generate_id(self) -> str:
        """Generate a unique proposal ID."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        random_suffix = hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:6]
        return f"prop_{timestamp}_{random_suffix}"
    
    def create_extract_proposal(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        tags: Optional[list[str]] = None,
        classification: Optional[str] = None,
        description: str = "",
    ) -> Proposal:
        """
        Create a proposal to extract tagged memories from one branch to another.
        
        This is the recommended way to share context between branches
        (instead of full merges).
        """
        proposal_id = self._generate_id()
        
        # Find matching entries
        entries = self._find_matching_entries(
            source_branch,
            tags=tags,
            classification=classification,
        )
        
        proposal = Proposal(
            id=proposal_id,
            type=ProposalType.EXTRACT,
            source_branch=source_branch,
            target_branch=target_branch,
            status=ProposalStatus.DRAFT,
            title=title,
            description=description,
            entries=entries,
            filters={
                "tags": tags or [],
                "classification": classification,
            },
        )
        
        self._save_proposal(proposal)
        return proposal
    
    def create_remember_proposal(
        self,
        target_branch: str,
        content: str,
        title: str,
        file_path: str = "memory/proposed.md",
        tags: Optional[list[str]] = None,
        classification: str = "private",
    ) -> Proposal:
        """
        Create a proposal to add new memory (requires human approval).
        """
        proposal_id = self._generate_id()
        
        entry = MemoryEntry(
            file_path=file_path,
            content=content,
            tags=tags or [],
            classification=classification,
        )
        
        proposal = Proposal(
            id=proposal_id,
            type=ProposalType.REMEMBER,
            source_branch="",  # No source
            target_branch=target_branch,
            status=ProposalStatus.PENDING,  # Immediately pending for review
            title=title,
            entries=[entry],
        )
        
        self._save_proposal(proposal)
        return proposal
    
    def create_forget_proposal(
        self,
        target_branch: str,
        memory_ids: list[str],
        title: str,
        reason: str,
    ) -> Proposal:
        """
        Create a proposal to remove memories (requires human approval).
        """
        proposal_id = self._generate_id()
        
        # Find the memories to forget
        entries = []
        for mid in memory_ids:
            # Find memory by ID
            memory = self._find_memory_by_id(target_branch, mid)
            if memory:
                memory.redacted = True
                memory.redaction_reason = reason
                entries.append(memory)
        
        proposal = Proposal(
            id=proposal_id,
            type=ProposalType.FORGET,
            source_branch="",
            target_branch=target_branch,
            status=ProposalStatus.PENDING,
            title=title,
            description=reason,
            entries=entries,
        )
        
        self._save_proposal(proposal)
        return proposal
    
    def _find_matching_entries(
        self,
        branch: str,
        tags: Optional[list[str]] = None,
        classification: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Find memory entries matching the given filters."""
        entries = []
        
        # Save current branch
        current = self.repo.repo.active_branch.name
        
        try:
            # Checkout source branch
            self.repo.repo.heads[branch].checkout()
            
            # Scan memory files
            for pattern in ["MEMORY.md", "memory/**/*.md"]:
                for file_path in self.repo.path.glob(pattern):
                    if ".gam" in str(file_path):
                        continue
                    
                    content = file_path.read_text()
                    rel_path = str(file_path.relative_to(self.repo.path))
                    
                    # Parse sections
                    sections = self._parse_sections(content)
                    
                    for section in sections:
                        # Check tag filter
                        if tags and not any(t in section.get("tags", []) for t in tags):
                            continue
                        
                        # Check classification filter
                        if classification and section.get("classification") != classification:
                            continue
                        
                        entries.append(MemoryEntry(
                            file_path=rel_path,
                            content=section["content"],
                            section=section.get("title"),
                            tags=section.get("tags", []),
                            classification=section.get("classification", "private"),
                        ))
        finally:
            # Restore original branch
            self.repo.repo.heads[current].checkout()
        
        return entries
    
    def _parse_sections(self, content: str) -> list[dict]:
        """Parse markdown content into sections with frontmatter."""
        sections = []
        
        # Try to parse YAML frontmatter
        fm_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
        file_meta = {}
        if fm_match:
            try:
                file_meta = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                pass
            content = content[fm_match.end():]
        
        # Split by H2
        parts = re.split(r'^## ', content, flags=re.MULTILINE)
        
        if len(parts) > 1:
            for part in parts[1:]:
                lines = part.split('\n', 1)
                title = lines[0].strip()
                body = lines[1].strip() if len(lines) > 1 else ""
                
                sections.append({
                    "title": title,
                    "content": f"## {title}\n{body}",
                    "tags": file_meta.get("tags", []),
                    "classification": file_meta.get("classification", "private"),
                })
        else:
            # No H2 sections
            sections.append({
                "title": None,
                "content": content,
                "tags": file_meta.get("tags", []),
                "classification": file_meta.get("classification", "private"),
            })
        
        return sections
    
    def _find_memory_by_id(self, branch: str, memory_id: str) -> Optional[MemoryEntry]:
        """Find a specific memory by ID."""
        # memory_id format: file_path:content_hash
        if ":" not in memory_id:
            return None
        
        file_path = memory_id.rsplit(":", 1)[0]
        
        current = self.repo.repo.active_branch.name
        try:
            self.repo.repo.heads[branch].checkout()
            
            full_path = self.repo.path / file_path
            if not full_path.exists():
                return None
            
            content = full_path.read_text()
            return MemoryEntry(file_path=file_path, content=content)
        finally:
            self.repo.repo.heads[current].checkout()
    
    def _save_proposal(self, proposal: Proposal):
        """Save a proposal to disk."""
        data = {
            "id": proposal.id,
            "type": proposal.type.value,
            "source_branch": proposal.source_branch,
            "target_branch": proposal.target_branch,
            "status": proposal.status.value,
            "title": proposal.title,
            "description": proposal.description,
            "created_at": proposal.created_at.isoformat(),
            "created_by": proposal.created_by,
            "filters": proposal.filters,
            "reviewed_by": proposal.reviewed_by,
            "reviewed_at": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
            "review_notes": proposal.review_notes,
            "commit_sha": proposal.commit_sha,
            "entries": [
                {
                    "file_path": e.file_path,
                    "content": e.content,
                    "section": e.section,
                    "tags": e.tags,
                    "classification": e.classification,
                    "redacted": e.redacted,
                    "redaction_reason": e.redaction_reason,
                }
                for e in proposal.entries
            ],
        }
        
        with open(self._proposal_path(proposal.id), "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    
    def _load_proposal(self, proposal_id: str) -> Optional[Proposal]:
        """Load a proposal from disk."""
        path = self._proposal_path(proposal_id)
        if not path.exists():
            return None
        
        with open(path) as f:
            data = yaml.safe_load(f)
        
        entries = [
            MemoryEntry(
                file_path=e["file_path"],
                content=e["content"],
                section=e.get("section"),
                tags=e.get("tags", []),
                classification=e.get("classification", "private"),
                redacted=e.get("redacted", False),
                redaction_reason=e.get("redaction_reason"),
            )
            for e in data.get("entries", [])
        ]
        
        return Proposal(
            id=data["id"],
            type=ProposalType(data["type"]),
            source_branch=data["source_branch"],
            target_branch=data["target_branch"],
            status=ProposalStatus(data["status"]),
            title=data["title"],
            description=data.get("description", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
            created_by=data.get("created_by", "gam"),
            entries=entries,
            filters=data.get("filters", {}),
            reviewed_by=data.get("reviewed_by"),
            reviewed_at=datetime.fromisoformat(data["reviewed_at"]) if data.get("reviewed_at") else None,
            review_notes=data.get("review_notes", ""),
            commit_sha=data.get("commit_sha"),
        )
    
    def get(self, proposal_id: str) -> Optional[Proposal]:
        """Get a proposal by ID."""
        return self._load_proposal(proposal_id)
    
    def list_proposals(
        self,
        status: Optional[ProposalStatus] = None,
        target_branch: Optional[str] = None,
    ) -> list[Proposal]:
        """List all proposals, optionally filtered."""
        proposals = []
        
        for path in self.proposals_dir.glob("prop_*.yaml"):
            proposal = self._load_proposal(path.stem)
            if not proposal:
                continue
            
            if status and proposal.status != status:
                continue
            
            if target_branch and proposal.target_branch != target_branch:
                continue
            
            proposals.append(proposal)
        
        return sorted(proposals, key=lambda p: p.created_at, reverse=True)
    
    def submit(self, proposal_id: str) -> Proposal:
        """Submit a draft proposal for review."""
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.DRAFT:
            raise ValueError(f"Proposal must be in DRAFT status (currently: {proposal.status.value})")
        
        proposal.status = ProposalStatus.PENDING
        self._save_proposal(proposal)
        return proposal
    
    def approve(
        self,
        proposal_id: str,
        reviewed_by: str = "human",
        notes: str = "",
    ) -> Proposal:
        """Approve a pending proposal."""
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal must be in PENDING status (currently: {proposal.status.value})")
        
        proposal.status = ProposalStatus.APPROVED
        proposal.reviewed_by = reviewed_by
        proposal.reviewed_at = datetime.now(timezone.utc)
        proposal.review_notes = notes
        self._save_proposal(proposal)
        return proposal
    
    def reject(
        self,
        proposal_id: str,
        reviewed_by: str = "human",
        reason: str = "",
    ) -> Proposal:
        """Reject a pending proposal."""
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal must be in PENDING status")
        
        proposal.status = ProposalStatus.REJECTED
        proposal.reviewed_by = reviewed_by
        proposal.reviewed_at = datetime.now(timezone.utc)
        proposal.review_notes = reason
        self._save_proposal(proposal)
        return proposal
    
    def merge(self, proposal_id: str) -> Proposal:
        """
        Merge an approved proposal into the target branch.
        
        This applies the memory changes (extract/remember/forget).
        """
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError(f"Proposal must be APPROVED before merging")
        
        # Save current branch
        current = self.repo.repo.active_branch.name
        
        try:
            # Checkout target branch
            self.repo.repo.heads[proposal.target_branch].checkout()
            
            if proposal.type == ProposalType.EXTRACT:
                # Write extracted entries to target
                commit_sha = self._apply_extract(proposal)
            elif proposal.type == ProposalType.REMEMBER:
                # Add new memory
                commit_sha = self._apply_remember(proposal)
            elif proposal.type == ProposalType.FORGET:
                # Remove memories
                commit_sha = self._apply_forget(proposal)
            else:
                raise ValueError(f"Unsupported proposal type: {proposal.type}")
            
            proposal.status = ProposalStatus.MERGED
            proposal.commit_sha = commit_sha
            self._save_proposal(proposal)
            
        finally:
            self.repo.repo.heads[current].checkout()
        
        return proposal
    
    def _apply_extract(self, proposal: Proposal) -> str:
        """Apply an extract proposal."""
        # Group entries by file
        by_file: dict[str, list[MemoryEntry]] = {}
        for entry in proposal.entries:
            if entry.redacted:
                continue  # Skip redacted entries
            
            target_file = f"memory/extracted/{proposal.source_branch.replace('/', '_')}.md"
            if target_file not in by_file:
                by_file[target_file] = []
            by_file[target_file].append(entry)
        
        # Write files
        for file_path, entries in by_file.items():
            full_path = self.repo.path / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            content = f"# Extracted from {proposal.source_branch}\n\n"
            content += f"_Proposal: {proposal.id}_\n\n"
            
            for entry in entries:
                content += entry.content + "\n\n---\n\n"
            
            full_path.write_text(content)
            self.repo.repo.index.add([file_path])
        
        # Commit
        message = f"gam: Extract memory from {proposal.source_branch}\n\nProposal: {proposal.id}\n{proposal.title}"
        commit = self.repo.repo.index.commit(message)
        
        return commit.hexsha
    
    def _apply_remember(self, proposal: Proposal) -> str:
        """Apply a remember proposal."""
        for entry in proposal.entries:
            full_path = self.repo.path / entry.file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Append to file or create new
            if full_path.exists():
                existing = full_path.read_text()
                content = existing + "\n\n" + entry.content
            else:
                content = entry.content
            
            full_path.write_text(content)
            self.repo.repo.index.add([entry.file_path])
        
        message = f"gam: Remember - {proposal.title}\n\nProposal: {proposal.id}"
        commit = self.repo.repo.index.commit(message)
        
        return commit.hexsha
    
    def _apply_forget(self, proposal: Proposal) -> str:
        """Apply a forget proposal (soft delete via redaction marker)."""
        for entry in proposal.entries:
            full_path = self.repo.path / entry.file_path
            if not full_path.exists():
                continue
            
            # Add redaction marker to file
            content = full_path.read_text()
            redaction_note = f"\n\n<!-- REDACTED: {entry.redaction_reason} -->\n"
            content = content.replace(entry.content, f"[REDACTED]{redaction_note}")
            
            full_path.write_text(content)
            self.repo.repo.index.add([entry.file_path])
        
        message = f"gam: Forget - {proposal.title}\n\nProposal: {proposal.id}\nReason: {proposal.description}"
        commit = self.repo.repo.index.commit(message)
        
        return commit.hexsha
    
    def remove_entry(self, proposal_id: str, entry_index: int) -> Proposal:
        """Remove an entry from a draft proposal."""
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.DRAFT:
            raise ValueError("Can only modify DRAFT proposals")
        
        if entry_index < 0 or entry_index >= len(proposal.entries):
            raise ValueError(f"Invalid entry index: {entry_index}")
        
        proposal.entries.pop(entry_index)
        self._save_proposal(proposal)
        return proposal
    
    def redact_entry(
        self,
        proposal_id: str,
        entry_index: int,
        reason: str,
    ) -> Proposal:
        """Mark an entry as redacted in a draft proposal."""
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        if proposal.status != ProposalStatus.DRAFT:
            raise ValueError("Can only modify DRAFT proposals")
        
        if entry_index < 0 or entry_index >= len(proposal.entries):
            raise ValueError(f"Invalid entry index: {entry_index}")
        
        proposal.entries[entry_index].redacted = True
        proposal.entries[entry_index].redaction_reason = reason
        self._save_proposal(proposal)
        return proposal
