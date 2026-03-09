#!/usr/bin/env python3
"""
CIA Verification Script

Demonstrates how to verify Conversation Integrity via MCP.
Run this after any agent run to audit LLM interactions.

Usage:
    python verify-cia.py
    python verify-cia.py --run-id run-abc123
"""

import os
import sys
import argparse
import httpx
import json

MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")


def check_status(run_id: str = None):
    """Check if CIA is enabled and what mode it's in."""
    print("=" * 60)
    print("CIA STATUS")
    print("=" * 60)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/status", json={
        "run_id": run_id
    })
    
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return False
    
    data = resp.json()
    print(f"  Enabled:       {data.get('enabled')}")
    print(f"  Mode:          {data.get('mode')}")
    print(f"  Version:       {data.get('cia_version')}")
    print(f"  Provider Path: {data.get('provider_path')}")
    print(f"  Scope:         {data.get('scope')}")
    
    if "stats" in data:
        stats = data["stats"]
        print(f"\n  Stats:")
        print(f"    Validated: {stats.get('total_validated', 0)}")
        print(f"    Valid:     {stats.get('valid', 0)}")
        print(f"    Repaired:  {stats.get('repaired', 0)}")
        print(f"    Rejected:  {stats.get('rejected', 0)}")
    
    return data.get('enabled', False)


def check_report(run_id: str = None):
    """Get integrity summary."""
    print("\n" + "=" * 60)
    print("CIA INTEGRITY REPORT")
    print("=" * 60)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/report", json={
        "run_id": run_id
    })
    
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    total = data.get('total_validated', 0)
    valid = data.get('valid', 0)
    repaired = data.get('repaired', 0)
    rejected = data.get('rejected', 0)
    
    print(f"  Total Validated: {total}")
    print(f"  Valid:           {valid} ({100*valid/total:.1f}%)" if total else "  Valid:           0")
    print(f"  Repaired:        {repaired}")
    print(f"  Rejected:        {rejected}")
    
    if repaired > 0:
        print(f"\n  ⚠️  {repaired} conversations required repair")
    if rejected > 0:
        print(f"\n  ❌ {rejected} conversations were rejected")


def check_repairs(run_id: str = None, limit: int = 10):
    """List any repairs that were made."""
    print("\n" + "=" * 60)
    print("CIA REPAIRS")
    print("=" * 60)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/repairs", json={
        "run_id": run_id,
        "limit": limit
    })
    
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    repairs = data.get('repairs', [])
    
    if not repairs:
        print("  ✅ No repairs needed")
        return
    
    print(f"  Found {len(repairs)} repair(s):\n")
    for r in repairs:
        print(f"  [{r.get('seq')}] {r.get('timestamp')}")
        print(f"      Reason:   {r.get('reason_code')}")
        print(f"      Severity: {r.get('severity')}")
        print(f"      Original: {r.get('original_hash', '')[:40]}...")
        print(f"      Repaired: {r.get('repaired_hash', '')[:40]}...")
        print()


def check_receipts(run_id: str = None, limit: int = 10):
    """Show LLM call receipts."""
    print("\n" + "=" * 60)
    print("CIA RECEIPTS (LLM Call Hashes)")
    print("=" * 60)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/receipts", json={
        "run_id": run_id,
        "limit": limit
    })
    
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    receipts = data.get('receipts', [])
    
    if not receipts:
        print("  No receipts found")
        return
    
    print(f"  Showing {len(receipts)} recent call(s):\n")
    for r in receipts:
        print(f"  [{r.get('seq')}] {r.get('timestamp')}")
        print(f"      Model:    {r.get('model')}")
        print(f"      Request:  {r.get('request_sha256', '')[:50]}...")
        print(f"      Response: {r.get('response_sha256', '')[:50]}...")
        
        # If hashes match, no repair was needed
        if r.get('request_sha256') == r.get('response_sha256'):
            print(f"      Status:   ✅ Valid (no repair)")
        else:
            print(f"      Status:   🔧 Repaired")
        print()


def main():
    parser = argparse.ArgumentParser(description="Verify CIA (Conversation Integrity)")
    parser.add_argument("--run-id", help="Scope to specific run")
    parser.add_argument("--limit", type=int, default=10, help="Max items to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    print(f"\n🔍 CIA Verification")
    print(f"   MCP Server: {MCP_URL}")
    if args.run_id:
        print(f"   Run ID: {args.run_id}")
    print()
    
    # Run all checks
    enabled = check_status(args.run_id)
    
    if not enabled:
        print("\n⚠️  CIA is not enabled. Conversation integrity is not being tracked.")
        sys.exit(1)
    
    check_report(args.run_id)
    check_repairs(args.run_id, args.limit)
    check_receipts(args.run_id, args.limit)
    
    print("\n" + "=" * 60)
    print("✅ CIA Verification Complete")
    print("=" * 60)
    print("\nCIA ensures tool_use/tool_result pairing is valid.")
    print("Receipts prove LLM calls happened without exposing content.")
    print()


if __name__ == "__main__":
    main()
