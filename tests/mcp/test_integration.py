"""
MCP Integration Test - Tests all governance tools
"""
import httpx
import json
import time
import subprocess
import sys
import os

MCP_URL = "http://127.0.0.1:3457"

def test_full_workflow():
    print("=" * 60)
    print("MCP Integration Test")
    print("=" * 60)
    
    results = []
    
    # Test 1: Start a run
    print("\n1. Starting governed run...")
    resp = httpx.post(f"{MCP_URL}/tools/run/start", json={
        "project_id": "test-project",
        "agent_ref": "integration-test:v1",
        "metadata": {"test": True}
    })
    data = resp.json()
    run_id = data["run_id"]
    print(f"   ✓ Run started: {run_id}")
    print(f"   Policy hash: {data['policy_hash']}")
    results.append(("run.start", True))
    
    # Test 2: Policy check (allowed)
    print("\n2. Policy check: web_search...")
    resp = httpx.post(f"{MCP_URL}/tools/policy/check", json={
        "run_id": run_id,
        "action": "web_search"
    })
    data = resp.json()
    print(f"   {'✓' if data['allow'] else '✗'} web_search: {data['reason']}")
    results.append(("policy.check (allow)", data["allow"]))
    
    # Test 3: Policy check (denied)
    print("\n3. Policy check: shell_exec...")
    resp = httpx.post(f"{MCP_URL}/tools/policy/check", json={
        "run_id": run_id,
        "action": "shell_exec"
    })
    data = resp.json()
    denied = not data["allow"]
    print(f"   {'✓' if denied else '✗'} shell_exec denied: {data['reason']}")
    results.append(("policy.check (deny)", denied))
    
    # Test 4: Web search (governed)
    print("\n4. Governed web search...")
    resp = httpx.post(f"{MCP_URL}/tools/web_search", json={
        "run_id": run_id,
        "query": "AI governance frameworks"
    })
    data = resp.json()
    has_results = "results" in data
    print(f"   ✓ Search executed, {len(data.get('results', []))} results")
    print(f"   Ledger entry: {data.get('ledger_entry_hash', 'N/A')[:30]}...")
    results.append(("tools.web_search", has_results))
    
    # Test 5: Memory write
    print("\n5. Memory write with provenance...")
    resp = httpx.post(f"{MCP_URL}/tools/memory/write", json={
        "run_id": run_id,
        "type": "insight",
        "content": "AI governance requires transparency and accountability.",
        "tags": ["governance", "insight"]
    })
    data = resp.json()
    has_memory = "memory_id" in data
    print(f"   ✓ Memory stored: {data.get('memory_id', 'N/A')}")
    print(f"   Commit hash: {data.get('commit_hash', 'N/A')}")
    print(f"   Ledger link: {data.get('ledger_entry_hash', 'N/A')[:30]}...")
    results.append(("memory.write", has_memory))
    
    # Test 6: Get timeline
    print("\n6. Getting audit timeline...")
    resp = httpx.post(f"{MCP_URL}/tools/ledger/timeline", json={
        "run_id": run_id
    })
    data = resp.json()
    entry_count = len(data.get("entries", []))
    chain_valid = data.get("chain_valid", False)
    print(f"   ✓ Timeline retrieved: {entry_count} entries")
    print(f"   Chain valid: {'✓' if chain_valid else '✗'}")
    results.append(("ledger.timeline", entry_count > 0))
    results.append(("chain_integrity", chain_valid))
    
    # Test 7: End run
    print("\n7. Ending run...")
    resp = httpx.post(f"{MCP_URL}/tools/run/end", json={
        "run_id": run_id
    })
    data = resp.json()
    print(f"   ✓ Run ended: {data['entries']} entries, chain valid: {data['chain_valid']}")
    results.append(("run.end", data["chain_valid"]))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, v in results if v)
    total = len(results)
    for name, passed_test in results:
        print(f"  {'✓' if passed_test else '✗'} {name}")
    print(f"\n{passed}/{total} tests passed")
    
    return passed == total


if __name__ == "__main__":
    try:
        success = test_full_workflow()
        sys.exit(0 if success else 1)
    except httpx.ConnectError:
        print("ERROR: Cannot connect to MCP server at", MCP_URL)
        print("Make sure the server is running: substr8 mcp start")
        sys.exit(1)
