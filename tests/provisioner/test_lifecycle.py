#!/usr/bin/env python3
"""
FDAA Agent Provisioning Lifecycle Test

Tests the full provisioning flow against a running FDAA API.

Usage:
    python3 tests/provisioner/test_lifecycle.py

Environment:
    FDAA_API_URL: FDAA API endpoint (default: http://localhost:18766)
    FDAA_AGENTS_PATH: Where to create agent workspaces (default: /tmp/fdaa-test-agents)
"""

import asyncio
import os
import sys
import json
import shutil

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import httpx

FDAA_API = os.environ.get("FDAA_API_URL", "http://localhost:18766")
FDAA_AGENTS_PATH = os.environ.get("FDAA_AGENTS_PATH", "/tmp/fdaa-test-agents")


async def test_lifecycle():
    """Run the full provisioning lifecycle test."""
    print("=" * 60)
    print("FDAA Agent Provisioning Lifecycle Test")
    print("=" * 60)
    print(f"API: {FDAA_API}")
    print(f"Agents Path: {FDAA_AGENTS_PATH}")
    print()

    # Set env for provisioner
    os.environ["FDAA_AGENTS_PATH"] = FDAA_AGENTS_PATH

    from fdaa_proxy.openclaw.provisioner import OpenClawProvisioner

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Health check
        print("[0/5] Checking FDAA API health...")
        try:
            resp = await client.get(f"{FDAA_API}/health")
            if resp.status_code != 200:
                print(f"  ✗ API not healthy: {resp.status_code}")
                return False
            print("  ✓ API healthy")
        except Exception as e:
            print(f"  ✗ Cannot connect to API: {e}")
            print(f"\n  Make sure FDAA API is running at {FDAA_API}")
            return False

        # Step 2: List agents
        print("\n[1/5] Fetching agents from FDAA Registry...")
        resp = await client.get(f"{FDAA_API}/v1/agents")
        if resp.status_code != 200:
            print(f"  ✗ Could not fetch agents: {resp.status_code}")
            return False

        agents = resp.json()
        agent_count = agents.get("count", len(agents.get("agents", [])))
        print(f"  ✓ Found {agent_count} agents")

        if agent_count == 0:
            print("  ✗ No agents registered. Register one first.")
            return False

        # Use first agent for test
        agent_list = agents.get("agents", agents)
        if isinstance(agent_list, list) and len(agent_list) > 0:
            test_agent_id = agent_list[0].get("id")
        else:
            print("  ✗ Could not parse agent list")
            return False

        # Step 3: Get agent details
        print(f"\n[2/5] Fetching agent '{test_agent_id}' details...")
        resp = await client.get(f"{FDAA_API}/v1/agents/{test_agent_id}")
        if resp.status_code != 200:
            print(f"  ✗ Could not fetch agent: {resp.status_code}")
            return False

        agent = resp.json()
        print(f"  ✓ Agent: {agent.get('name', test_agent_id)}")
        print(f"  ✓ Hash: {agent.get('current_hash', 'unknown')[:16]}...")
        print(f"  ✓ Version: {agent.get('current_version', 'unknown')}")

        # Step 4: Get system prompt
        print(f"\n[3/5] Fetching agent's system prompt...")
        resp = await client.get(f"{FDAA_API}/v1/agents/{test_agent_id}/prompt")
        if resp.status_code != 200:
            print(f"  ✗ Could not fetch prompt: {resp.status_code}")
            # Try without prompt endpoint
            system_prompt = "You are a helpful assistant."
            print(f"  ! Using default prompt")
        else:
            prompt_data = resp.json()
            system_prompt = prompt_data.get("system_prompt", "")
            print(f"  ✓ Prompt length: {len(system_prompt)} chars")
            hash_match = prompt_data.get("hash") == agent.get("current_hash")
            print(f"  ✓ Hash verified: {hash_match}")

        # Step 5: Generate OpenClaw config
        print(f"\n[4/5] Generating OpenClaw agent config...")
        provisioner = OpenClawProvisioner()

        try:
            config = provisioner.generate_openclaw_agent_config(
                agent_id=test_agent_id,
                agent_hash=agent.get("current_hash", "test-hash"),
                version=agent.get("current_version", 1),
                system_prompt=system_prompt,
                name=agent.get("name", test_agent_id),
                allowed_tools=agent.get("allowed_tools", ["*"]),
            )
            print(f"  ✓ OpenClaw ID: {config['id']}")
            print(f"  ✓ Workspace: {config['workspace']}")
            print(f"  ✓ AgentDir: {config['agentDir']}")
        except Exception as e:
            print(f"  ✗ Config generation failed: {e}")
            return False

        # Step 6: Verify workspace
        print(f"\n[5/5] Verifying workspace and security isolation...")
        workspace = config["workspace"]

        # Check workspace exists
        if not os.path.exists(workspace):
            print(f"  ✗ Workspace not created: {workspace}")
            return False
        print(f"  ✓ Workspace exists: True")

        # Check permissions
        perms = oct(os.stat(workspace).st_mode)[-3:]
        print(f"  ✓ Directory permissions: {perms} (700 = owner only)")

        # Check files
        agents_md = os.path.join(workspace, "AGENTS.md")
        fdaa_json = os.path.join(workspace, ".fdaa.json")
        print(f"  ✓ AGENTS.md exists: {os.path.exists(agents_md)}")
        print(f"  ✓ .fdaa.json exists: {os.path.exists(fdaa_json)}")

        # Verify provenance
        if os.path.exists(fdaa_json):
            with open(fdaa_json) as f:
                provenance = json.load(f)
            hash_match = provenance.get("agent_hash") == agent.get("current_hash")
            print(f"  ✓ Provenance hash matches: {hash_match}")

        # Check isolation
        main_workspace = os.path.expanduser("~/.openclaw/workspace")
        isolated = workspace != main_workspace and not workspace.startswith(main_workspace)
        print(f"  ✓ Workspace isolated from main: {isolated}")

        # Check no path traversal possible
        no_traverse = ".." not in workspace
        print(f"  ✓ No path traversal: {no_traverse}")

        # Check no memory leak
        agent_files = set(os.listdir(workspace))
        no_memory_leak = "MEMORY.md" not in agent_files and "USER.md" not in agent_files
        print(f"  ✓ No memory files leaked: {no_memory_leak}")
        print(f"  ✓ Agent workspace contents: {list(agent_files)}")

        # Check schema compliance
        has_system_prompt_in_identity = "systemPrompt" in config.get("identity", {})
        print(f"  ✓ No systemPrompt in identity: {not has_system_prompt_in_identity}")

        # Results
        print("\n" + "=" * 60)
        all_passed = all(
            [
                os.path.exists(workspace),
                perms == "700",
                os.path.exists(agents_md),
                os.path.exists(fdaa_json),
                isolated,
                no_traverse,
                no_memory_leak,
                not has_system_prompt_in_identity,
            ]
        )

        if all_passed:
            print("✅ ALL LIFECYCLE TESTS PASSED")
        else:
            print("❌ SOME TESTS FAILED")
        print("=" * 60)

        # Cleanup
        print("\nCleaning up test workspace...")
        if os.path.exists(workspace):
            shutil.rmtree(workspace)
            print(f"  ✓ Removed: {workspace}")

        return all_passed


if __name__ == "__main__":
    success = asyncio.run(test_lifecycle())
    sys.exit(0 if success else 1)
