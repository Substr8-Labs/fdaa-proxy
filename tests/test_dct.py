"""Tests for the DCT Audit Logger."""

import pytest
from datetime import datetime, timezone

from fdaa_proxy.dct import DCTLogger, DCTEntry


@pytest.fixture
def memory_logger():
    """Create an in-memory logger for testing."""
    return DCTLogger(storage="memory")


def test_log_entry(memory_logger):
    """Test logging an entry."""
    entry = memory_logger.log(
        event_type="tool_call",
        gateway_id="test-gateway",
        tool="get_file",
        arguments={"path": "/test"},
        persona="ada"
    )
    
    assert entry.id.startswith("dct_")
    assert entry.event_type == "tool_call"
    assert entry.gateway_id == "test-gateway"
    assert entry.tool == "get_file"
    assert entry.entry_hash is not None


def test_hash_chain(memory_logger):
    """Test hash chain linking."""
    entry1 = memory_logger.log(
        event_type="tool_call",
        gateway_id="test",
        tool="tool1"
    )
    
    entry2 = memory_logger.log(
        event_type="tool_call",
        gateway_id="test",
        tool="tool2"
    )
    
    # Entry 2 should link to entry 1
    assert entry2.prev_hash == entry1.entry_hash


def test_chain_verification(memory_logger):
    """Test chain verification."""
    # Log several entries
    for i in range(10):
        memory_logger.log(
            event_type="tool_call",
            gateway_id="test",
            tool=f"tool_{i}"
        )
    
    # Verify chain
    result = memory_logger.verify_chain()
    assert result.valid is True
    assert result.entries_checked == 10


def test_query(memory_logger):
    """Test querying entries."""
    # Log entries for different gateways
    memory_logger.log(event_type="tool_call", gateway_id="gateway-a", tool="tool1")
    memory_logger.log(event_type="tool_call", gateway_id="gateway-b", tool="tool2")
    memory_logger.log(event_type="error", gateway_id="gateway-a", tool="tool3")
    
    # Query by gateway
    entries = memory_logger.query(gateway_id="gateway-a")
    assert len(entries) == 2
    
    # Query by event type
    entries = memory_logger.query(event_type="error")
    assert len(entries) == 1


def test_entry_hash_determinism():
    """Test that entry hashes are deterministic."""
    entry = DCTEntry(
        id="test-1",
        timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        event_type="tool_call",
        gateway_id="test",
        tool="get_file",
        prev_hash=None
    )
    
    hash1 = entry.compute_hash()
    hash2 = entry.compute_hash()
    
    assert hash1 == hash2


def test_stats(memory_logger):
    """Test logger statistics."""
    memory_logger.log(event_type="test", gateway_id="test")
    memory_logger.log(event_type="test", gateway_id="test")
    
    stats = memory_logger.stats
    assert stats["storage"] == "memory"
    assert stats["entry_count"] == 2
    assert stats["last_hash"] is not None
