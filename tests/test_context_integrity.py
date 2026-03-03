"""
Tests for Context Integrity Adapter (CIA).

Covers the 5 corruption scenarios from the spec:
1. Truncate history mid-tool-transaction → entire transaction removed
2. Gateway restart during tool execution → tool marked failed
3. Concurrent tool calls → serialized correctly
4. Replay session with orphaned result → orphan auto-pruned
5. Manual load with corrupt history → validator catches with clear error
"""

import pytest
from typing import Any

from adapters.context_integrity import (
    ContextIntegrityAdapter,
    CIAConfig,
    Mode,
    ValidationResult,
    IntegrityViolation,
    IntegrityEvent,
    ContextIntegrityError,
    validate_tool_pairing,
    repair_context,
    find_safe_truncation_point,
    has_unresolved_tool_uses,
    create_adapter,
    Message,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def valid_conversation() -> list[Message]:
    """A valid conversation with tool usage."""
    return [
        {"role": "user", "content": "What's the weather in Sydney?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "content": "I'll check the weather for you."},
                {
                    "type": "tool_use",
                    "id": "toolu_001",
                    "name": "get_weather",
                    "input": {"location": "Sydney"}
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_001",
                    "content": "Sydney: 24°C, sunny"
                }
            ]
        },
        {
            "role": "assistant",
            "content": "The weather in Sydney is 24°C and sunny!"
        }
    ]


@pytest.fixture
def orphaned_tool_result_conversation() -> list[Message]:
    """Conversation with orphaned tool_result (references non-existent tool_use)."""
    return [
        {"role": "user", "content": "Tell me a joke"},
        {"role": "assistant", "content": "Why did the chicken cross the road?"},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_ghost",
                    "content": "This result has no matching tool_use!"
                }
            ]
        },
        {"role": "assistant", "content": "To get to the other side!"}
    ]


@pytest.fixture
def dangling_tool_use_conversation() -> list[Message]:
    """Conversation with dangling tool_use (no result)."""
    return [
        {"role": "user", "content": "Search for something"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_search",
                    "name": "web_search",
                    "input": {"query": "OpenClaw"}
                }
            ]
        },
        # Missing tool_result message - jumped straight to next assistant message
        {"role": "assistant", "content": "I found some results..."}
    ]


@pytest.fixture
def multi_tool_conversation() -> list[Message]:
    """Conversation with multiple concurrent tool calls."""
    return [
        {"role": "user", "content": "Check weather and calendar"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_weather",
                    "name": "get_weather",
                    "input": {"location": "Sydney"}
                },
                {
                    "type": "tool_use",
                    "id": "toolu_calendar",
                    "name": "check_calendar",
                    "input": {"date": "2026-03-03"}
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_weather",
                    "content": "Sydney: 24°C"
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_calendar",
                    "content": "No events today"
                }
            ]
        },
        {"role": "assistant", "content": "Weather is 24°C and you have no events."}
    ]


@pytest.fixture
def dct_logger():
    """Mock DCT logger that collects events."""
    class MockDCTLogger:
        def __init__(self):
            self.events: list[IntegrityEvent] = []
        
        def log_event(self, event: IntegrityEvent) -> None:
            self.events.append(event)
    
    return MockDCTLogger()


# =============================================================================
# Scenario 1: Truncate history mid-tool-transaction
# =============================================================================

class TestTruncationScenario:
    """Test that truncation never splits tool transactions."""
    
    def test_find_safe_truncation_preserves_transaction(self, valid_conversation):
        """Truncation point should not split tool_use from tool_result."""
        # Try to truncate to just the tool result message
        # Should return index that includes both tool_use and tool_result
        safe_idx = find_safe_truncation_point(
            valid_conversation, 
            target_tokens=50,  # Small target
            from_end=True
        )
        
        # The safe index should be at most 1 (can remove first user message)
        # or at least include the full tool transaction
        remaining = valid_conversation[safe_idx:]
        
        # Check that if we have a tool_result, we also have its tool_use
        assert not has_unresolved_tool_uses(remaining), \
            "Truncation left unresolved tool transaction"
    
    def test_truncation_from_start_preserves_transaction(self, valid_conversation):
        """Truncation from start should also preserve transactions."""
        safe_idx = find_safe_truncation_point(
            valid_conversation,
            target_tokens=100,
            from_end=False
        )
        
        kept = valid_conversation[:safe_idx]
        
        # If we kept a tool_use, we should have kept its result too
        # (unless it's the very last message - pending execution)
        if len(kept) > 0:
            last_msg = kept[-1]
            if last_msg.get("role") == "assistant":
                # Check if it has tool_uses
                tool_uses = [
                    b for b in last_msg.get("content", []) 
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                if tool_uses:
                    # This should be the last message, or have results
                    assert safe_idx == len(valid_conversation) or \
                           not has_unresolved_tool_uses(kept)
    
    def test_mid_transaction_truncation_removes_entire_transaction(self):
        """If target is mid-transaction, entire transaction should be excluded."""
        messages = [
            {"role": "user", "content": "Long preamble " * 100},  # ~200 tokens
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "test", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}
                ]
            },
            {"role": "assistant", "content": "Done!"}
        ]
        
        # Try to get just enough tokens to include tool_use but not result
        # Safe truncation should either include both or neither
        safe_idx = find_safe_truncation_point(messages, target_tokens=220, from_end=True)
        remaining = messages[safe_idx:]
        
        # Verify no orphaned tools
        violations = validate_tool_pairing(remaining)
        tool_violations = [v for v in violations if "tool" in v["type"]]
        assert len(tool_violations) == 0


# =============================================================================
# Scenario 2: Gateway restart during tool execution
# =============================================================================

class TestGatewayRestartScenario:
    """Test handling of interrupted tool execution (dangling tool_use)."""
    
    def test_dangling_tool_use_detected(self, dangling_tool_use_conversation):
        """Dangling tool_use should be detected as violation."""
        violations = validate_tool_pairing(dangling_tool_use_conversation)
        
        assert len(violations) > 0
        assert any(v["type"] == "dangling_tool_use" for v in violations)
        
        dangling = next(v for v in violations if v["type"] == "dangling_tool_use")
        assert dangling["tool_use_id"] == "toolu_search"
    
    def test_dangling_tool_use_repaired_with_synthetic_failure(
        self, dangling_tool_use_conversation
    ):
        """Dangling tool_use should get synthetic failure injected."""
        repaired, events = repair_context(dangling_tool_use_conversation)
        
        # Should have injected a synthetic failure
        assert len(events) > 0
        assert any(e["action"] == "inject_synthetic_failure" for e in events)
        
        # Repaired messages should now be valid
        violations = validate_tool_pairing(repaired)
        dangling_violations = [v for v in violations if v["type"] == "dangling_tool_use"]
        assert len(dangling_violations) == 0
    
    def test_synthetic_failure_marked_as_error(self, dangling_tool_use_conversation):
        """Synthetic failure results should have is_error=True."""
        repaired, _ = repair_context(dangling_tool_use_conversation)
        
        # Find the injected tool_result
        for msg in repaired:
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if block.get("tool_use_id") == "toolu_search":
                            assert block.get("is_error") is True
                            assert "[CIA]" in block.get("content", "")
                            return
        
        pytest.fail("Could not find injected tool_result")
    
    def test_adapter_repair_mode_handles_restart(self, dangling_tool_use_conversation, dct_logger):
        """Adapter in repair mode should handle gateway restart scenario."""
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.REPAIR),
            dct_logger=dct_logger
        )
        
        result = adapter.validate(dangling_tool_use_conversation, session_id="test-session")
        
        assert result.valid is True
        assert result.repaired_messages is not None
        assert len(result.events) > 0
        
        # DCT should have logged the event
        assert len(dct_logger.events) > 0


# =============================================================================
# Scenario 3: Concurrent tool calls
# =============================================================================

class TestConcurrentToolCallsScenario:
    """Test handling of multiple tool calls in single message."""
    
    def test_valid_concurrent_calls_pass_validation(self, multi_tool_conversation):
        """Valid concurrent tool calls should pass validation."""
        violations = validate_tool_pairing(multi_tool_conversation)
        assert len(violations) == 0
    
    def test_partial_concurrent_results_detected(self):
        """Missing result for one of multiple concurrent calls should be detected."""
        messages = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_a", "name": "action_a", "input": {}},
                    {"type": "tool_use", "id": "toolu_b", "name": "action_b", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    # Only result for toolu_a, missing toolu_b
                    {"type": "tool_result", "tool_use_id": "toolu_a", "content": "done"}
                ]
            },
            {"role": "assistant", "content": "Completed"}
        ]
        
        violations = validate_tool_pairing(messages)
        
        assert len(violations) == 1
        assert violations[0]["type"] == "dangling_tool_use"
        assert violations[0]["tool_use_id"] == "toolu_b"
    
    def test_partial_concurrent_results_repaired(self):
        """Missing concurrent result should be repaired with synthetic failure."""
        messages = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_a", "name": "action_a", "input": {}},
                    {"type": "tool_use", "id": "toolu_b", "name": "action_b", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_a", "content": "done"}
                ]
            },
            {"role": "assistant", "content": "Completed"}
        ]
        
        repaired, events = repair_context(messages)
        
        # Should have injected failure for toolu_b
        assert any(
            e["tool_use_id"] == "toolu_b" and e["action"] == "inject_synthetic_failure"
            for e in events
        )
        
        # Should now be valid
        violations = validate_tool_pairing(repaired)
        assert len(violations) == 0
    
    def test_concurrent_ordering_preserved(self, multi_tool_conversation):
        """Concurrent tool results should maintain correspondence with tool_uses."""
        # Validation should pass
        violations = validate_tool_pairing(multi_tool_conversation)
        assert len(violations) == 0


# =============================================================================
# Scenario 4: Replay session with orphaned result
# =============================================================================

class TestOrphanedResultScenario:
    """Test handling of orphaned tool_results (referencing non-existent tool_use)."""
    
    def test_orphaned_result_detected(self, orphaned_tool_result_conversation):
        """Orphaned tool_result should be detected."""
        violations = validate_tool_pairing(orphaned_tool_result_conversation)
        
        assert len(violations) > 0
        orphan_violations = [v for v in violations if v["type"] == "orphaned_tool_result"]
        assert len(orphan_violations) == 1
        assert orphan_violations[0]["tool_use_id"] == "toolu_ghost"
    
    def test_orphaned_result_pruned(self, orphaned_tool_result_conversation):
        """Orphaned tool_result should be pruned in repair mode."""
        repaired, events = repair_context(orphaned_tool_result_conversation)
        
        # Should have logged the prune event
        assert any(e["action"] == "prune_orphaned_tool_result" for e in events)
        
        # Orphan should be removed
        violations = validate_tool_pairing(repaired)
        orphan_violations = [v for v in violations if v["type"] == "orphaned_tool_result"]
        assert len(orphan_violations) == 0
    
    def test_valid_content_preserved_after_prune(self):
        """Non-orphan content in same message should be preserved."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_real", "name": "test", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_real", "content": "ok"},
                    {"type": "tool_result", "tool_use_id": "toolu_ghost", "content": "orphan"},
                    {"type": "text", "content": "Additional text"}
                ]
            }
        ]
        
        repaired, events = repair_context(messages)
        
        # Find the user message
        user_msg = next(m for m in repaired if m.get("role") == "user" and isinstance(m.get("content"), list))
        content = user_msg["content"]
        
        # Valid tool_result should remain
        assert any(
            b.get("tool_use_id") == "toolu_real" 
            for b in content if b.get("type") == "tool_result"
        )
        
        # Orphan should be removed
        assert not any(
            b.get("tool_use_id") == "toolu_ghost" 
            for b in content if b.get("type") == "tool_result"
        )
        
        # Text should be preserved
        assert any(b.get("type") == "text" for b in content)
    
    def test_adapter_log_only_mode_preserves_orphans(
        self, orphaned_tool_result_conversation, dct_logger
    ):
        """Log-only mode should detect but not remove orphans."""
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.LOG_ONLY),
            dct_logger=dct_logger
        )
        
        result = adapter.validate(orphaned_tool_result_conversation)
        
        # Should be marked valid (log-only doesn't fail)
        assert result.valid is True
        
        # But violations should be recorded
        assert len(result.violations) > 0
        
        # DCT should have logged
        assert len(dct_logger.events) > 0
        
        # Original messages unchanged (no repaired_messages)
        assert result.repaired_messages is None


# =============================================================================
# Scenario 5: Manual load with corrupt history
# =============================================================================

class TestCorruptHistoryScenario:
    """Test detection of various corrupt history patterns."""
    
    def test_tool_result_at_start_detected(self):
        """Tool result at conversation start should be detected."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_nowhere", "content": "???"}
                ]
            },
            {"role": "assistant", "content": "Hello"}
        ]
        
        violations = validate_tool_pairing(messages)
        
        assert len(violations) > 0
        assert violations[0]["type"] == "orphaned_tool_result"
        assert "start of conversation" in violations[0]["details"]
    
    def test_tool_result_after_user_message_detected(self):
        """Tool result after non-assistant message should be detected."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_x", "content": "oops"}
            ]}
        ]
        
        violations = validate_tool_pairing(messages)
        
        assert any(v["type"] == "orphaned_tool_result" for v in violations)
    
    def test_consecutive_same_role_detected(self):
        """Consecutive messages with same role should be flagged."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello again"},  # Should be assistant
            {"role": "assistant", "content": "Hi there"}
        ]
        
        violations = validate_tool_pairing(messages)
        
        assert any(v["type"] == "message_ordering" for v in violations)
    
    def test_strict_mode_rejects_corrupt_history(self, dct_logger):
        """Strict mode should reject corrupt history."""
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_bad", "content": "corrupt"}
            ]}
        ]
        
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.STRICT),
            dct_logger=dct_logger
        )
        
        result = adapter.validate(messages, session_id="corrupt-session")
        
        assert result.valid is False
        assert len(result.violations) > 0
        
        # Should have logged rejection
        assert len(dct_logger.events) > 0
        assert any(e["action"] == "reject" for e in dct_logger.events)
    
    def test_corrupt_history_provides_clear_error(self):
        """Violations should have clear, actionable error messages."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_123", "name": "test", "input": {}}
            ]},
            {"role": "assistant", "content": "No result message!"}
        ]
        
        violations = validate_tool_pairing(messages)
        
        assert len(violations) > 0
        
        for v in violations:
            assert "message_index" in v
            assert "details" in v
            assert len(v["details"]) > 10  # Meaningful message


# =============================================================================
# Additional Tests: Edge Cases and Configuration
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_messages(self):
        """Empty message list should be valid."""
        violations = validate_tool_pairing([])
        assert len(violations) == 0
    
    def test_text_only_conversation(self):
        """Conversation without tools should be valid."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm good!"}
        ]
        violations = validate_tool_pairing(messages)
        assert len(violations) == 0
    
    def test_pending_tool_use_valid(self):
        """Tool use in last message (pending execution) should be valid."""
        messages = [
            {"role": "user", "content": "Search for something"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_pending", "name": "search", "input": {}}
                ]
            }
        ]
        
        violations = validate_tool_pairing(messages)
        # No dangling violation - last message is allowed to have pending tool_use
        dangling = [v for v in violations if v["type"] == "dangling_tool_use"]
        assert len(dangling) == 0
    
    def test_string_content_handled(self):
        """Messages with string content (not list) should be handled."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        
        # Should not raise
        violations = validate_tool_pairing(messages)
        assert len(violations) == 0
    
    def test_mixed_content_types(self):
        """Message with mixed text and tool blocks should work."""
        messages = [
            {"role": "user", "content": "Check the weather please"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "content": "Let me check that for you."},
                    {"type": "tool_use", "id": "toolu_mix", "name": "weather", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_mix", "content": "Sunny"},
                    {"type": "text", "content": "Thanks!"}
                ]
            },
            {"role": "assistant", "content": "It's sunny!"}
        ]
        
        violations = validate_tool_pairing(messages)
        assert len(violations) == 0


class TestConfiguration:
    """Test configuration options."""
    
    def test_create_adapter_factory(self):
        """Factory function should create configured adapter."""
        adapter = create_adapter(mode="strict")
        assert adapter.config.mode == Mode.STRICT
        
        adapter = create_adapter(mode="repair", prune_orphans=False)
        assert adapter.config.mode == Mode.REPAIR
        assert adapter.config.prune_orphans is False
    
    def test_disable_specific_validations(self):
        """Should be able to disable specific validation checks."""
        config = CIAConfig(
            validate_tool_result_references=False,
            validate_dangling_tool_uses=True
        )
        # Config is stored but validation function uses all rules
        # Individual flag checks would be in middleware layer
        assert config.validate_tool_result_references is False
    
    def test_callable_dct_logger(self):
        """Should accept callable as DCT logger."""
        events_logged = []
        
        def log_fn(event: IntegrityEvent) -> None:
            events_logged.append(event)
        
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.REPAIR),
            dct_logger=log_fn
        )
        
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "orphan", "content": "x"}
            ]}
        ]
        
        adapter.validate(messages)
        
        assert len(events_logged) > 0
    
    def test_disabled_dct_logging(self):
        """Should not log when DCT disabled."""
        events_logged = []
        
        def log_fn(event: IntegrityEvent) -> None:
            events_logged.append(event)
        
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.REPAIR, dct_enabled=False),
            dct_logger=log_fn
        )
        
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "orphan", "content": "x"}
            ]}
        ]
        
        adapter.validate(messages)
        
        assert len(events_logged) == 0


class TestHasUnresolvedToolUses:
    """Test the has_unresolved_tool_uses helper function."""
    
    def test_resolved_tools(self, valid_conversation):
        """Fully resolved tool calls should return False."""
        assert has_unresolved_tool_uses(valid_conversation) is False
    
    def test_pending_tool(self):
        """Pending (last message) tool should be unresolved."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "pending", "name": "test", "input": {}}
                ]
            }
        ]
        assert has_unresolved_tool_uses(messages) is True
    
    def test_partial_resolution(self):
        """Partially resolved concurrent calls should be unresolved."""
        messages = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "a", "name": "test", "input": {}},
                    {"type": "tool_use", "id": "b", "name": "test", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": "done"}
                    # Missing b
                ]
            }
        ]
        assert has_unresolved_tool_uses(messages) is True


class TestContextIntegrityError:
    """Test the ContextIntegrityError exception."""
    
    def test_error_message_formatting(self):
        """Error should have informative message."""
        violations = [
            {"type": "orphaned_tool_result", "message_index": 5, "tool_use_id": "x", "details": "test", "severity": "error"},
            {"type": "dangling_tool_use", "message_index": 10, "tool_use_id": "y", "details": "test", "severity": "error"},
        ]
        
        error = ContextIntegrityError(violations)
        
        assert "orphaned_tool_result at index 5" in str(error)
        assert "dangling_tool_use at index 10" in str(error)
        assert error.violations == violations
    
    def test_truncates_long_violation_list(self):
        """Should truncate message for many violations."""
        violations = [
            {"type": f"violation_{i}", "message_index": i, "tool_use_id": None, "details": "x", "severity": "error"}
            for i in range(10)
        ]
        
        error = ContextIntegrityError(violations)
        
        assert "+7 more" in str(error)


# =============================================================================
# Integration Tests
# =============================================================================

class TestFullIntegration:
    """End-to-end integration tests."""
    
    def test_repair_then_validate_workflow(self, dangling_tool_use_conversation, dct_logger):
        """Full repair → validate workflow should work."""
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.REPAIR),
            dct_logger=dct_logger
        )
        
        # First validation (will repair)
        result = adapter.validate(dangling_tool_use_conversation, session_id="test")
        
        assert result.valid is True
        assert result.repaired_messages is not None
        
        # Second validation of repaired messages should pass clean
        result2 = adapter.validate(result.repaired_messages, session_id="test")
        
        assert result2.valid is True
        assert len(result2.violations) == 0
        assert result2.repaired_messages is None  # No repairs needed
    
    def test_truncate_then_validate_workflow(self, valid_conversation):
        """Truncation followed by validation should produce valid context."""
        adapter = ContextIntegrityAdapter(config=CIAConfig(mode=Mode.STRICT))
        
        # Find safe truncation point
        safe_idx = adapter.find_safe_truncation(valid_conversation, target_tokens=50)
        truncated = valid_conversation[safe_idx:]
        
        # Truncated messages should be valid
        result = adapter.validate(truncated)
        
        # May have violations due to missing start, but no tool pairing issues
        tool_violations = [
            v for v in result.violations 
            if v["type"] in ("orphaned_tool_result", "dangling_tool_use")
        ]
        assert len(tool_violations) == 0
    
    def test_complex_multi_turn_conversation(self, dct_logger):
        """Test a complex multi-turn conversation with various patterns."""
        messages = [
            {"role": "user", "content": "Start a complex task"},
            
            # First tool call
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "content": "I'll help with that."},
                    {"type": "tool_use", "id": "step1", "name": "init", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "step1", "content": "initialized"}
                ]
            },
            
            # Concurrent tool calls
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "step2a", "name": "fetch_a", "input": {}},
                    {"type": "tool_use", "id": "step2b", "name": "fetch_b", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "step2a", "content": "data_a"},
                    {"type": "tool_result", "tool_use_id": "step2b", "content": "data_b"}
                ]
            },
            
            # Final step
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "content": "Processing..."},
                    {"type": "tool_use", "id": "step3", "name": "process", "input": {}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "step3", "content": "complete"}
                ]
            },
            
            {"role": "assistant", "content": "Task completed successfully!"}
        ]
        
        adapter = ContextIntegrityAdapter(
            config=CIAConfig(mode=Mode.STRICT),
            dct_logger=dct_logger
        )
        
        result = adapter.validate(messages, session_id="complex-test")
        
        assert result.valid is True
        assert len(result.violations) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
