"""
RIL → GAM Promotion Worker

Handles selective promotion of RIL events to GAM memory.
- Deterministic selection rules
- Renders events as markdown artifacts
- Commits to GAM git repo
- Links back to ledger
- Creates DCT receipts

This is the bridge between hot execution telemetry and cold agent memory.
"""

import os
import json
import logging
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone

from .ledger_v2 import (
    WorkLedgerV2, Event, EventType, PromotionState, hash_payload
)

logger = logging.getLogger("fdaa-ril-promotion")


# =============================================================================
# Promotion Rules
# =============================================================================

@dataclass
class PromotionRule:
    """A rule that determines if an event should be promoted."""
    event_type: EventType
    min_attention: float = 0.0
    always_promote: bool = False
    condition: Optional[Callable[[Event], bool]] = None
    
    def matches(self, event: Event) -> bool:
        """Check if this rule matches the event."""
        if event.event_type != self.event_type:
            return False
        if self.always_promote:
            return True
        if event.attention < self.min_attention:
            return False
        if self.condition and not self.condition(event):
            return False
        return True


# Default promotion rules
DEFAULT_RULES: List[PromotionRule] = [
    # Always promote high-integrity events
    PromotionRule(EventType.CONTEXT_REPAIRED, always_promote=True),
    PromotionRule(EventType.CAPABILITY_DENIED, always_promote=True),
    PromotionRule(EventType.CRASH_RECOVERY, always_promote=True),
    PromotionRule(EventType.DECISION_POINT, always_promote=True),
    
    # Promote tool completions with errors or long duration
    PromotionRule(
        EventType.TOOL_COMPLETED,
        condition=lambda e: (
            e.payload.get("error") is not None or
            (e.payload.get("duration_ms") or 0) > 5000
        ),
    ),
    
    # Promote turn completions with high attention
    PromotionRule(EventType.TURN_COMPLETED, min_attention=0.7),
    
    # Don't promote message_received or low-attention tool_invoked
]


def should_promote(event: Event, rules: List[PromotionRule] = None) -> bool:
    """Determine if an event should be promoted to GAM."""
    rules = rules or DEFAULT_RULES
    
    for rule in rules:
        if rule.matches(event):
            return True
    
    # Default: promote if attention >= 0.7
    return event.attention >= 0.7


# =============================================================================
# Artifact Rendering
# =============================================================================

def render_event_artifact(event: Event) -> str:
    """
    Render an event as a markdown artifact for GAM.
    
    Format:
    - YAML frontmatter with metadata
    - Human-readable body
    """
    # Frontmatter
    frontmatter = {
        "event_id": event.event_id,
        "turn_id": event.turn_id,
        "event_type": event.event_type.value,
        "payload_hash": event.payload_hash,
        "source": "ril",
        "session_id": event.session_id,
        "agent_ref": event.agent_ref,
        "attention": event.attention,
        "created_at": event.event_ts,
    }
    
    # Body based on event type
    payload = event.payload or {}
    
    if event.event_type == EventType.CONTEXT_REPAIRED:
        body = _render_context_repaired(payload)
    elif event.event_type == EventType.CAPABILITY_DENIED:
        body = _render_capability_denied(payload)
    elif event.event_type == EventType.CRASH_RECOVERY:
        body = _render_crash_recovery(payload)
    elif event.event_type == EventType.TOOL_COMPLETED:
        body = _render_tool_completed(payload)
    elif event.event_type == EventType.DECISION_POINT:
        body = _render_decision_point(payload)
    else:
        body = _render_generic(event.event_type, payload)
    
    # Combine
    yaml_lines = ["---"]
    for k, v in frontmatter.items():
        if v is not None:
            yaml_lines.append(f"{k}: {json.dumps(v)}")
    yaml_lines.append("---")
    yaml_lines.append("")
    
    return "\n".join(yaml_lines) + body


def _render_context_repaired(payload: Dict[str, Any]) -> str:
    repairs = payload.get("repairs_applied", [])
    lines = ["# Context Repaired", ""]
    lines.append("The execution context was repaired due to integrity violations.")
    lines.append("")
    
    if repairs:
        lines.append("## Repairs Applied")
        for r in repairs:
            rtype = r.get("type", "unknown")
            lines.append(f"- **{rtype}**")
            if r.get("tool_use_id"):
                lines.append(f"  - tool_use_id: `{r['tool_use_id']}`")
            if r.get("tool_name"):
                lines.append(f"  - tool: `{r['tool_name']}`")
    
    return "\n".join(lines)


def _render_capability_denied(payload: Dict[str, Any]) -> str:
    tool = payload.get("tool", "unknown")
    reason = payload.get("reason", "No reason provided")
    
    lines = ["# Capability Denied", ""]
    lines.append(f"Tool **`{tool}`** was blocked by policy.")
    lines.append("")
    lines.append(f"**Reason:** {reason}")
    
    return "\n".join(lines)


def _render_crash_recovery(payload: Dict[str, Any]) -> str:
    pending_tools = payload.get("pending_tools", [])
    pending_tasks = payload.get("pending_tasks", [])
    
    lines = ["# Crash Recovery", ""]
    lines.append("System recovered from an interrupted execution state.")
    lines.append("")
    
    if pending_tools:
        lines.append("## Pending Tool Calls")
        for t in pending_tools:
            lines.append(f"- `{t.get('tool_name', 'unknown')}` (id: {t.get('tool_use_id', '?')})")
    
    if pending_tasks:
        lines.append("")
        lines.append("## Pending Tasks")
        for t in pending_tasks:
            lines.append(f"- {t.get('intent', 'unknown task')}")
    
    return "\n".join(lines)


def _render_tool_completed(payload: Dict[str, Any]) -> str:
    tool = payload.get("tool", "unknown")
    error = payload.get("error")
    duration = payload.get("duration_ms")
    
    lines = [f"# Tool Completed: {tool}", ""]
    
    if error:
        lines.append(f"**Status:** Error")
        lines.append(f"**Error:** {error}")
    else:
        lines.append(f"**Status:** Success")
    
    if duration:
        lines.append(f"**Duration:** {duration}ms")
    
    return "\n".join(lines)


def _render_decision_point(payload: Dict[str, Any]) -> str:
    decision = payload.get("decision", "No decision recorded")
    context = payload.get("context", "")
    
    lines = ["# Decision Point", ""]
    lines.append(f"**Decision:** {decision}")
    if context:
        lines.append("")
        lines.append(f"**Context:** {context}")
    
    return "\n".join(lines)


def _render_generic(event_type: EventType, payload: Dict[str, Any]) -> str:
    lines = [f"# {event_type.value.replace('_', ' ').title()}", ""]
    
    if payload:
        lines.append("## Payload")
        lines.append("```json")
        lines.append(json.dumps(payload, indent=2))
        lines.append("```")
    
    return "\n".join(lines)


# =============================================================================
# GAM Repository Manager
# =============================================================================

@dataclass
class GAMRepoConfig:
    """Configuration for GAM git repository."""
    repo_path: Path
    remote_url: Optional[str] = None
    branch: str = "main"
    push_on_commit: bool = False
    push_interval_seconds: int = 3600  # 1 hour default


class GAMRepo:
    """
    Manages the GAM git repository.
    
    Handles:
    - Initialization
    - Committing artifacts
    - Pushing to remote
    """
    
    def __init__(self, config: GAMRepoConfig):
        self.config = config
        self.repo_path = config.repo_path
        self._ensure_repo()
    
    def _ensure_repo(self):
        """Ensure the repo exists and is initialized."""
        self.repo_path.mkdir(parents=True, exist_ok=True)
        git_dir = self.repo_path / ".git"
        
        if not git_dir.exists():
            self._run_git("init")
            logger.info(f"Initialized GAM repo at {self.repo_path}")
            
            # Create initial structure
            (self.repo_path / "memory").mkdir(exist_ok=True)
            (self.repo_path / "memory" / "events").mkdir(exist_ok=True)
            
            # Initial commit
            readme = self.repo_path / "README.md"
            readme.write_text("# GAM Memory Repository\n\nGit-native agent memory.\n")
            self._run_git("add", ".")
            self._run_git("commit", "-m", "Initialize GAM repository")
        
        if self.config.remote_url:
            try:
                self._run_git("remote", "get-url", "origin")
            except subprocess.CalledProcessError:
                self._run_git("remote", "add", "origin", self.config.remote_url)
                logger.info(f"Added remote: {self.config.remote_url}")
    
    def _run_git(self, *args: str) -> str:
        """Run a git command in the repo."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    
    def commit_artifact(
        self,
        event: Event,
        content: str,
    ) -> tuple[str, str]:
        """
        Commit an artifact to the repo.
        
        Returns (commit_sha, file_path).
        """
        # Determine path
        ts = datetime.fromisoformat(event.event_ts.replace('Z', '+00:00'))
        rel_path = Path("memory") / "events" / ts.strftime("%Y") / ts.strftime("%m") / ts.strftime("%d")
        full_dir = self.repo_path / rel_path
        full_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{event.event_id}.md"
        file_path = rel_path / filename
        full_path = self.repo_path / file_path
        
        # Check idempotency
        if full_path.exists():
            logger.debug(f"Artifact already exists: {file_path}")
            # Get current commit
            commit_sha = self._run_git("rev-parse", "HEAD")
            return commit_sha, str(file_path)
        
        # Write file
        full_path.write_text(content)
        
        # Commit
        self._run_git("add", str(file_path))
        commit_msg = f"ril: promote {event.event_type.value} {event.event_id[:12]}"
        self._run_git("commit", "-m", commit_msg)
        
        # Get commit SHA
        commit_sha = self._run_git("rev-parse", "HEAD")
        
        logger.info(f"Committed artifact: {file_path} ({commit_sha[:8]})")
        
        # Push if configured
        if self.config.push_on_commit:
            self.push()
        
        return commit_sha, str(file_path)
    
    def push(self) -> bool:
        """Push to remote if configured."""
        if not self.config.remote_url:
            return False
        
        try:
            self._run_git("push", "-u", "origin", self.config.branch)
            logger.info("Pushed to remote")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Push failed: {e}")
            return False
    
    def get_head_sha(self) -> str:
        """Get current HEAD commit SHA."""
        return self._run_git("rev-parse", "HEAD")


# =============================================================================
# DCT Receipt Writer
# =============================================================================

def write_dct_receipt(
    event: Event,
    artifact_content: str,
    gam_commit: str,
    gam_path: str,
    dct_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Write a DCT receipt for a promotion.
    
    The receipt provides cryptographic proof of the promotion.
    """
    receipt = {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "payload_hash": event.payload_hash,
        "artifact_hash": f"sha256:{hashlib.sha256(artifact_content.encode()).hexdigest()}",
        "gam_commit": gam_commit,
        "gam_path": gam_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_ref": event.agent_ref,
        "session_id": event.session_id,
    }
    
    if dct_path:
        dct_path.parent.mkdir(parents=True, exist_ok=True)
        # Append to receipts file
        with open(dct_path, "a") as f:
            f.write(json.dumps(receipt) + "\n")
    
    return receipt


# =============================================================================
# Promotion Worker
# =============================================================================

class PromotionWorker:
    """
    Handles RIL → GAM promotion.
    
    Lifecycle:
    1. Select events for promotion (rules-based)
    2. Render artifacts (markdown with frontmatter)
    3. Commit to GAM repo
    4. Link back to ledger
    5. Create DCT receipts
    """
    
    def __init__(
        self,
        ledger: WorkLedgerV2,
        gam_repo: GAMRepo,
        rules: Optional[List[PromotionRule]] = None,
        dct_path: Optional[Path] = None,
    ):
        self.ledger = ledger
        self.gam_repo = gam_repo
        self.rules = rules or DEFAULT_RULES
        self.dct_path = dct_path or Path("./data/dct_receipts.jsonl")
        
        self._stats = {
            "runs": 0,
            "promoted": 0,
            "skipped": 0,
            "errors": 0,
        }
    
    def run_batch(self, limit: int = 100) -> Dict[str, Any]:
        """
        Run a batch promotion.
        
        Returns stats about what was promoted.
        """
        self._stats["runs"] += 1
        batch_stats = {"promoted": 0, "skipped": 0, "errors": 0}
        
        # Get events for promotion
        events = self.ledger.get_events_for_promotion(limit=limit)
        
        for event in events:
            try:
                if should_promote(event, self.rules):
                    # Queue it
                    self.ledger.mark_event_queued(event.event_id)
                    
                    # Render artifact
                    content = render_event_artifact(event)
                    
                    # Commit to GAM
                    gam_commit, gam_path = self.gam_repo.commit_artifact(event, content)
                    
                    # Link back to ledger
                    self.ledger.mark_event_promoted(event.event_id, gam_commit, gam_path)
                    
                    # Write DCT receipt
                    write_dct_receipt(
                        event=event,
                        artifact_content=content,
                        gam_commit=gam_commit,
                        gam_path=gam_path,
                        dct_path=self.dct_path,
                    )
                    
                    batch_stats["promoted"] += 1
                    self._stats["promoted"] += 1
                    logger.debug(f"Promoted: {event.event_id}")
                    
                else:
                    # Skip low-priority events
                    self.ledger.mark_event_skipped(event.event_id)
                    batch_stats["skipped"] += 1
                    self._stats["skipped"] += 1
                    
            except Exception as e:
                logger.error(f"Promotion error for {event.event_id}: {e}")
                batch_stats["errors"] += 1
                self._stats["errors"] += 1
        
        logger.info(f"Promotion batch: {batch_stats}")
        return batch_stats
    
    def promote_immediate(self, event: Event) -> bool:
        """
        Immediately promote a high-priority event.
        
        Use for context_repaired, crash_recovery, etc.
        """
        try:
            content = render_event_artifact(event)
            gam_commit, gam_path = self.gam_repo.commit_artifact(event, content)
            self.ledger.mark_event_promoted(event.event_id, gam_commit, gam_path)
            
            write_dct_receipt(
                event=event,
                artifact_content=content,
                gam_commit=gam_commit,
                gam_path=gam_path,
                dct_path=self.dct_path,
            )
            
            self._stats["promoted"] += 1
            logger.info(f"Immediate promotion: {event.event_id}")
            return True
            
        except Exception as e:
            logger.error(f"Immediate promotion failed: {e}")
            self._stats["errors"] += 1
            return False
    
    def get_stats(self) -> Dict[str, int]:
        return self._stats.copy()


# =============================================================================
# Factory Functions
# =============================================================================

def create_promotion_worker(
    ledger: WorkLedgerV2,
    gam_repo_path: Optional[Path] = None,
    gam_remote_url: Optional[str] = None,
    dct_path: Optional[Path] = None,
) -> PromotionWorker:
    """Factory function to create a promotion worker."""
    gam_path = gam_repo_path or Path("./data/gam_repo")
    
    config = GAMRepoConfig(
        repo_path=gam_path,
        remote_url=gam_remote_url,
        push_on_commit=gam_remote_url is not None,
    )
    
    gam_repo = GAMRepo(config)
    
    return PromotionWorker(
        ledger=ledger,
        gam_repo=gam_repo,
        dct_path=dct_path,
    )
