#!/usr/bin/env python3
"""
Test Agent Registry functionality.

Run: python3 -m pytest tests/test_agent_registry.py -v
Or:  python3 tests/test_agent_registry.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fdaa_proxy.agents import (
    AgentRegistry,
    AgentStorage,
    AgentCreate,
    AgentUpdate,
    AgentRollback,
    SpawnRequest,
    PersonaFile,
)


def test_agent_crud():
    """Test basic CRUD operations."""
    # Use temp db
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        storage = AgentStorage(db_path=db_path)
        registry = AgentRegistry(storage=storage)
        
        # Create agent
        print("Creating agent...")
        create_req = AgentCreate(
            id="val",
            name="Val",
            description="CFO of Control Tower",
            files=[
                PersonaFile(
                    filename="SOUL.md",
                    content="# Val\n\nYou are Val, CFO of Control Tower.\n\nPersonality: Analytical, strategic, calm."
                ),
                PersonaFile(
                    filename="IDENTITY.md", 
                    content="# Identity\n\n- Name: Val\n- Role: CFO\n- Emoji: 📊"
                ),
            ],
            created_by="test",
            commit_message="Initial version",
        )
        
        agent = registry.create(create_req)
        print(f"  ✓ Created: {agent.id} (hash: {agent.current_hash[:16]}...)")
        assert agent.id == "val"
        assert agent.name == "Val"
        assert agent.current_version == 1
        
        # Get agent
        print("Getting agent...")
        fetched = registry.get("val")
        assert fetched is not None
        assert fetched.current_hash == agent.current_hash
        print(f"  ✓ Fetched: {fetched.id}")
        
        # List agents
        print("Listing agents...")
        agents = registry.list()
        assert len(agents) == 1
        print(f"  ✓ Listed: {len(agents)} agent(s)")
        
        # Update agent
        print("Updating agent...")
        update_req = AgentUpdate(
            files=[
                PersonaFile(
                    filename="SOUL.md",
                    content="# Val\n\nYou are Val, CFO of Control Tower.\n\nPersonality: Analytical, strategic, calm.\n\nVoice: Measured, precise."
                ),
                PersonaFile(
                    filename="IDENTITY.md",
                    content="# Identity\n\n- Name: Val\n- Role: CFO\n- Emoji: 📊"
                ),
            ],
            commit_message="Added voice section",
            updated_by="test",
        )
        
        updated = registry.update("val", update_req)
        assert updated is not None
        assert updated.current_version == 2
        assert updated.current_hash != agent.current_hash
        print(f"  ✓ Updated: v{updated.current_version} (hash: {updated.current_hash[:16]}...)")
        
        # List versions
        print("Listing versions...")
        versions = registry.list_versions("val")
        assert len(versions) == 2
        print(f"  ✓ Versions: {[v.version for v in versions]}")
        
        # Get system prompt
        print("Getting system prompt...")
        prompt = registry.get_system_prompt("val")
        assert "Val" in prompt
        assert "CFO" in prompt
        print(f"  ✓ System prompt: {len(prompt)} chars")
        
        # Rollback
        print("Rolling back...")
        rollback_req = AgentRollback(
            version=1,
            rolled_back_by="test",
            reason="Testing rollback",
        )
        rolled_back = registry.rollback("val", rollback_req)
        assert rolled_back is not None
        assert rolled_back.current_version == 3  # Rollback creates new version
        assert rolled_back.current_hash == agent.current_hash  # Same as v1
        print(f"  ✓ Rolled back to v1 (now v{rolled_back.current_version})")
        
        # Delete
        print("Deleting agent...")
        deleted = registry.delete("val")
        assert deleted
        print("  ✓ Deleted")
        
        # Verify deleted
        assert registry.get("val") is None
        print("  ✓ Verified deletion")
        
        # Stats
        print("Getting stats...")
        stats = registry.stats()
        print(f"  ✓ Stats: {stats}")
        
        print("\n✅ All CRUD tests passed!")
        
    finally:
        os.unlink(db_path)


def test_hash_consistency():
    """Test that hashes are deterministic."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        storage = AgentStorage(db_path=db_path)
        registry = AgentRegistry(storage=storage)
        
        files = [
            PersonaFile(filename="SOUL.md", content="Test content"),
            PersonaFile(filename="IDENTITY.md", content="More content"),
        ]
        
        # Create agent
        agent1 = registry.create(AgentCreate(
            id="test1",
            name="Test 1",
            files=files,
        ))
        
        # Create another with same files
        agent2 = registry.create(AgentCreate(
            id="test2", 
            name="Test 2",
            files=files,
        ))
        
        # Hashes should match
        assert agent1.current_hash == agent2.current_hash
        print(f"✓ Hash consistency: {agent1.current_hash[:16]}...")
        
        # Different content = different hash
        agent3 = registry.create(AgentCreate(
            id="test3",
            name="Test 3",
            files=[
                PersonaFile(filename="SOUL.md", content="Different content"),
                PersonaFile(filename="IDENTITY.md", content="More content"),
            ],
        ))
        
        assert agent3.current_hash != agent1.current_hash
        print(f"✓ Different content = different hash: {agent3.current_hash[:16]}...")
        
        print("\n✅ Hash consistency tests passed!")
        
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    print("=" * 60)
    print("FDAA Agent Registry Tests")
    print("=" * 60)
    print()
    
    test_agent_crud()
    print()
    test_hash_consistency()
    
    print()
    print("=" * 60)
    print("All tests passed! ✅")
    print("=" * 60)
