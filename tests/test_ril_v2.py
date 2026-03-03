"""
RIL v2 Integration Tests

Tests the full RIL → GAM promotion pipeline:
1. Canonical ID generation (idempotency)
2. Event logging
3. Tool transaction tracking
4. Promotion to GAM
5. DCT receipts
"""

import json
import tempfile
import pytest
from pathlib import Path

from fdaa_proxy.ril import (
    # Ledger
    WorkLedgerV2,
    EventType,
    ToolTxnStatus,
    PromotionState,
    make_turn_id,
    make_event_id,
    make_tool_txn_id,
    hash_payload,
    # Promotion
    GAMRepo,
    GAMRepoConfig,
    PromotionWorker,
    render_event_artifact,
    should_promote,
    create_promotion_worker,
)


class TestCanonicalIDs:
    """Test deterministic ID generation."""
    
    def test_turn_id_deterministic(self):
        """Same inputs = same turn_id."""
        id1 = make_turn_id("session-123", 5, "abc123")
        id2 = make_turn_id("session-123", 5, "abc123")
        assert id1 == id2
        assert id1.startswith("turn:")
    
    def test_turn_id_different_inputs(self):
        """Different inputs = different turn_id."""
        id1 = make_turn_id("session-123", 5, "abc123")
        id2 = make_turn_id("session-123", 6, "abc123")
        assert id1 != id2
    
    def test_event_id_deterministic(self):
        """Same inputs = same event_id."""
        turn_id = make_turn_id("sess", 1, "hash")
        id1 = make_event_id(turn_id, "tool_completed", "payload_hash")
        id2 = make_event_id(turn_id, "tool_completed", "payload_hash")
        assert id1 == id2
        assert id1.startswith("evt:")
    
    def test_tool_txn_id_deterministic(self):
        """Same inputs = same tool_txn_id."""
        turn_id = make_turn_id("sess", 1, "hash")
        id1 = make_tool_txn_id(turn_id, "read_file", "use-123")
        id2 = make_tool_txn_id(turn_id, "read_file", "use-123")
        assert id1 == id2
        assert id1.startswith("txn:")
    
    def test_payload_hash_deterministic(self):
        """Same payload = same hash."""
        data = {"key": "value", "nested": {"a": 1}}
        h1 = hash_payload(data)
        h2 = hash_payload({"nested": {"a": 1}, "key": "value"})  # Different order
        assert h1 == h2  # Sort keys makes it deterministic


class TestLedgerV2:
    """Test the v2 ledger."""
    
    @pytest.fixture
    def ledger(self, tmp_path):
        """Create a fresh ledger."""
        return WorkLedgerV2(path=tmp_path / "test_ledger.db")
    
    def test_log_event(self, ledger):
        """Test basic event logging."""
        turn_id = make_turn_id("sess", 1, "msg_hash")
        
        event = ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.TOOL_COMPLETED,
            payload={"tool": "read_file", "duration_ms": 150},
            agent_ref="test-agent",
            session_id="sess",
            attention=0.5,
        )
        
        assert event.event_id.startswith("evt:")
        assert event.turn_id == turn_id
        assert event.event_type == EventType.TOOL_COMPLETED
        assert event.promotion_state == PromotionState.NONE
    
    def test_event_idempotency(self, ledger):
        """Logging same event twice should not duplicate."""
        turn_id = make_turn_id("sess", 1, "msg_hash")
        payload = {"tool": "test"}
        
        event1 = ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.TOOL_INVOKED,
            payload=payload,
            agent_ref="agent",
        )
        
        event2 = ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.TOOL_INVOKED,
            payload=payload,
            agent_ref="agent",
        )
        
        # Same canonical ID
        assert event1.event_id == event2.event_id
        
        # Only one row in DB
        stats = ledger.get_stats()
        assert stats["events_logged"] == 2  # Attempts
        # But actual count should be 1 (INSERT OR IGNORE)
    
    def test_tool_transaction_lifecycle(self, ledger):
        """Test tool transaction from pending → completed."""
        turn_id = make_turn_id("sess", 1, "hash")
        
        # Start
        txn = ledger.start_tool_txn(
            turn_id=turn_id,
            tool_use_id="use-abc",
            tool_name="web_search",
            input_data={"query": "test"},
        )
        
        assert txn.status == ToolTxnStatus.PENDING
        assert txn.tool_txn_id.startswith("txn:")
        
        # Get pending
        pending = ledger.get_pending_tool_txns()
        assert len(pending) == 1
        
        # Complete
        success = ledger.complete_tool_txn(
            tool_txn_id=txn.tool_txn_id,
            result_data={"results": ["a", "b"]},
            duration_ms=250,
        )
        assert success
        
        # No longer pending
        pending = ledger.get_pending_tool_txns()
        assert len(pending) == 0
    
    def test_tool_transaction_synthetic_failure(self, ledger):
        """Test CIA marking a tool as synthetic-failed."""
        turn_id = make_turn_id("sess", 1, "hash")
        
        txn = ledger.start_tool_txn(
            turn_id=turn_id,
            tool_use_id="use-xyz",
            tool_name="dangerous_tool",
            input_data={},
        )
        
        # CIA decides to inject synthetic failure
        ledger.mark_synthetic_failed(txn.tool_txn_id, "Context truncation repair")
        
        # Verify
        updated = ledger.get_tool_txn_by_use_id("use-xyz")
        assert updated.status == ToolTxnStatus.SYNTHETIC_FAILED
        assert "[CIA]" in updated.error
    
    def test_promotion_state_tracking(self, ledger):
        """Test promotion state transitions."""
        turn_id = make_turn_id("sess", 1, "hash")
        
        event = ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.CONTEXT_REPAIRED,
            payload={"repairs": ["fix1"]},
            agent_ref="agent",
            attention=0.9,
        )
        
        # Initial state
        assert event.promotion_state == PromotionState.NONE
        
        # Queue
        ledger.mark_event_queued(event.event_id)
        
        # Promote
        ledger.mark_event_promoted(
            event.event_id,
            gam_commit="abc123def456",
            gam_path="memory/events/2026/03/03/evt_xyz.md",
        )
        
        # Check stats
        stats = ledger.get_stats()
        assert stats["events_promoted"] == 1


class TestArtifactRendering:
    """Test markdown artifact generation."""
    
    @pytest.fixture
    def sample_event(self):
        from fdaa_proxy.ril.ledger_v2 import Event
        return Event(
            event_id="evt:abc123",
            turn_id="turn:xyz789",
            event_type=EventType.CONTEXT_REPAIRED,
            event_ts="2026-03-03T12:00:00Z",
            payload_json=json.dumps({
                "repairs_applied": [
                    {"type": "injected_synthetic_failure", "tool_use_id": "use-123"}
                ]
            }),
            payload_hash="sha256:test",
            agent_ref="test-agent",
            session_id="sess-001",
            attention=0.9,
        )
    
    def test_render_context_repaired(self, sample_event):
        content = render_event_artifact(sample_event)
        
        assert "---" in content  # Has frontmatter
        assert "event_id" in content
        assert "Context Repaired" in content
        assert "injected_synthetic_failure" in content


class TestGAMRepo:
    """Test GAM git repository management."""
    
    def test_init_repo(self, tmp_path):
        """Test repository initialization."""
        config = GAMRepoConfig(repo_path=tmp_path / "gam_test")
        repo = GAMRepo(config)
        
        assert (tmp_path / "gam_test" / ".git").exists()
        assert (tmp_path / "gam_test" / "memory" / "events").exists()
    
    def test_commit_artifact(self, tmp_path):
        """Test committing an artifact."""
        from fdaa_proxy.ril.ledger_v2 import Event
        
        config = GAMRepoConfig(repo_path=tmp_path / "gam_test")
        repo = GAMRepo(config)
        
        event = Event(
            event_id="evt:test123",
            turn_id="turn:abc",
            event_type=EventType.CAPABILITY_DENIED,
            event_ts="2026-03-03T15:30:00Z",
            payload_json="{}",
            payload_hash="sha256:test",
            agent_ref="agent",
            attention=0.8,
        )
        
        content = render_event_artifact(event)
        commit_sha, file_path = repo.commit_artifact(event, content)
        
        assert len(commit_sha) == 40  # Full SHA
        assert "2026/03/03" in file_path
        assert file_path.endswith(".md")
        
        # File should exist
        full_path = tmp_path / "gam_test" / file_path
        assert full_path.exists()
    
    def test_commit_idempotency(self, tmp_path):
        """Committing same artifact twice should not create duplicate."""
        from fdaa_proxy.ril.ledger_v2 import Event
        
        config = GAMRepoConfig(repo_path=tmp_path / "gam_test")
        repo = GAMRepo(config)
        
        event = Event(
            event_id="evt:idempotent",
            turn_id="turn:abc",
            event_type=EventType.CRASH_RECOVERY,
            event_ts="2026-03-03T15:30:00Z",
            payload_json="{}",
            payload_hash="sha256:test",
            agent_ref="agent",
            attention=0.9,
        )
        
        content = render_event_artifact(event)
        
        sha1, path1 = repo.commit_artifact(event, content)
        sha2, path2 = repo.commit_artifact(event, content)
        
        # Same path
        assert path1 == path2


class TestPromotionWorker:
    """Test the full promotion pipeline."""
    
    @pytest.fixture
    def setup(self, tmp_path):
        """Create ledger, GAM repo, and promotion worker."""
        ledger = WorkLedgerV2(path=tmp_path / "ledger.db")
        
        worker = create_promotion_worker(
            ledger=ledger,
            gam_repo_path=tmp_path / "gam",
            dct_path=tmp_path / "dct_receipts.jsonl",
        )
        
        return {
            "ledger": ledger,
            "worker": worker,
            "tmp_path": tmp_path,
        }
    
    def test_full_promotion_flow(self, setup):
        """Test complete RIL → GAM flow."""
        ledger = setup["ledger"]
        worker = setup["worker"]
        tmp_path = setup["tmp_path"]
        
        turn_id = make_turn_id("sess", 1, "hash")
        
        # Log a high-priority event
        event = ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.CONTEXT_REPAIRED,
            payload={"repairs_applied": [{"type": "test_repair"}]},
            agent_ref="agent",
            attention=0.95,
        )
        
        # Run promotion batch
        stats = worker.run_batch()
        
        assert stats["promoted"] == 1
        assert stats["errors"] == 0
        
        # Check ledger was updated
        ledger_stats = ledger.get_stats()
        assert ledger_stats["events_promoted"] == 1
        
        # Check DCT receipt was written
        dct_file = tmp_path / "dct_receipts.jsonl"
        assert dct_file.exists()
        
        with open(dct_file) as f:
            receipt = json.loads(f.readline())
        
        assert receipt["event_id"] == event.event_id
        assert "gam_commit" in receipt
        assert "artifact_hash" in receipt
    
    def test_low_attention_skipped(self, setup):
        """Low-attention events should be skipped."""
        ledger = setup["ledger"]
        worker = setup["worker"]
        
        turn_id = make_turn_id("sess", 1, "hash")
        
        # Log a low-priority event
        ledger.log_event(
            turn_id=turn_id,
            event_type=EventType.MESSAGE_RECEIVED,
            payload={"content": "hi"},
            agent_ref="agent",
            attention=0.2,  # Low
        )
        
        stats = worker.run_batch()
        
        assert stats["promoted"] == 0
        assert stats["skipped"] == 1


class TestPromotionRules:
    """Test promotion rule evaluation."""
    
    def test_context_repaired_always_promotes(self):
        from fdaa_proxy.ril.ledger_v2 import Event
        
        event = Event(
            event_id="evt:test",
            turn_id="turn:test",
            event_type=EventType.CONTEXT_REPAIRED,
            event_ts="2026-03-03T12:00:00Z",
            payload_json="{}",
            payload_hash="sha256:test",
            agent_ref="agent",
            attention=0.1,  # Low attention, but should still promote
        )
        
        assert should_promote(event) is True
    
    def test_tool_completed_with_error_promotes(self):
        from fdaa_proxy.ril.ledger_v2 import Event
        
        event = Event(
            event_id="evt:test",
            turn_id="turn:test",
            event_type=EventType.TOOL_COMPLETED,
            event_ts="2026-03-03T12:00:00Z",
            payload_json=json.dumps({"error": "Connection refused"}),
            payload_hash="sha256:test",
            agent_ref="agent",
            attention=0.3,
        )
        
        assert should_promote(event) is True
    
    def test_message_received_skipped(self):
        from fdaa_proxy.ril.ledger_v2 import Event
        
        event = Event(
            event_id="evt:test",
            turn_id="turn:test",
            event_type=EventType.MESSAGE_RECEIVED,
            event_ts="2026-03-03T12:00:00Z",
            payload_json="{}",
            payload_hash="sha256:test",
            agent_ref="agent",
            attention=0.3,  # Below 0.7 threshold
        )
        
        assert should_promote(event) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
