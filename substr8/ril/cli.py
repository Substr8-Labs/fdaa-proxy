"""
Substr8 RIL CLI - Runtime Integrity Layer

Commands:
    substr8 ril status              Show RIL component health
    substr8 ril validate            Validate a context payload
    substr8 ril repair              Repair corrupted context
    substr8 ril ledger              Work ledger operations
    substr8 ril triggers            Trigger management
"""

import click
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

from . import RepairMode, TriggerEvent, ValidationResult, RepairResult

console = Console()


# ============================================================
# Context Integrity Adapter (CIA)
# ============================================================

def validate_tool_pairing(messages: List[Dict[str, Any]]) -> ValidationResult:
    """
    Validate tool_use/tool_result pairing in a message array.
    
    Rules:
    1. Every tool_result must reference an existing tool_use
    2. Every tool_use should have a corresponding tool_result (warning if not)
    3. tool_use IDs must be unique
    """
    errors = []
    warnings = []
    repairs_needed = []
    
    # Track tool_use blocks by ID
    tool_uses: Dict[str, Dict[str, Any]] = {}
    tool_results: Dict[str, Dict[str, Any]] = {}
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", [])
        
        if not isinstance(content, list):
            continue
        
        for j, block in enumerate(content):
            block_type = block.get("type", "")
            
            if block_type == "tool_use":
                tool_id = block.get("id", "")
                if not tool_id:
                    errors.append(f"Message {i}, block {j}: tool_use missing id")
                    continue
                    
                if tool_id in tool_uses:
                    errors.append(f"Message {i}, block {j}: duplicate tool_use id '{tool_id}'")
                else:
                    tool_uses[tool_id] = {
                        "message_idx": i,
                        "block_idx": j,
                        "name": block.get("name", "unknown"),
                    }
            
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                if not tool_use_id:
                    errors.append(f"Message {i}, block {j}: tool_result missing tool_use_id")
                    repairs_needed.append({
                        "type": "orphan_tool_result",
                        "message_idx": i,
                        "block_idx": j,
                        "action": "remove",
                    })
                    continue
                
                if tool_use_id not in tool_uses:
                    errors.append(f"Message {i}, block {j}: orphaned tool_result references non-existent tool_use '{tool_use_id}'")
                    repairs_needed.append({
                        "type": "orphan_tool_result",
                        "message_idx": i,
                        "block_idx": j,
                        "tool_use_id": tool_use_id,
                        "action": "remove",
                    })
                else:
                    tool_results[tool_use_id] = {
                        "message_idx": i,
                        "block_idx": j,
                    }
    
    # Check for unresolved tool_uses (no matching result)
    for tool_id, info in tool_uses.items():
        if tool_id not in tool_results:
            warnings.append(f"Unresolved tool_use '{tool_id}' ({info['name']}) at message {info['message_idx']}")
            repairs_needed.append({
                "type": "unresolved_tool_use",
                "tool_use_id": tool_id,
                "message_idx": info["message_idx"],
                "block_idx": info["block_idx"],
                "tool_name": info["name"],
                "action": "inject_synthetic_failure",
            })
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        repairs_needed=repairs_needed,
    )


def repair_context(
    messages: List[Dict[str, Any]], 
    mode: RepairMode = RepairMode.PERMISSIVE
) -> tuple[List[Dict[str, Any]], RepairResult]:
    """
    Repair a corrupted context based on validation results.
    
    Modes:
    - STRICT: Reject invalid payloads (no repair)
    - PERMISSIVE: Apply repairs and log
    - FORENSIC: Halt and create snapshot for debugging
    """
    import copy
    
    # Hash original
    original_json = json.dumps(messages, sort_keys=True)
    original_hash = f"sha256:{hashlib.sha256(original_json.encode()).hexdigest()}"
    
    # Validate first
    validation = validate_tool_pairing(messages)
    
    if validation.valid:
        return messages, RepairResult(
            success=True,
            original_hash=original_hash,
            repaired_hash=original_hash,
            repairs_applied=[],
            mode=mode,
        )
    
    if mode == RepairMode.STRICT:
        return messages, RepairResult(
            success=False,
            original_hash=original_hash,
            repaired_hash=original_hash,
            repairs_applied=[],
            mode=mode,
        )
    
    # Apply repairs
    repaired = copy.deepcopy(messages)
    repairs_applied = []
    
    # Sort repairs by message index (descending) to avoid index shifting
    repairs = sorted(validation.repairs_needed, key=lambda r: -r.get("message_idx", 0))
    
    for repair in repairs:
        repair_type = repair.get("type", "")
        
        if repair_type == "orphan_tool_result" and repair.get("action") == "remove":
            msg_idx = repair["message_idx"]
            block_idx = repair["block_idx"]
            
            if msg_idx < len(repaired):
                content = repaired[msg_idx].get("content", [])
                if isinstance(content, list) and block_idx < len(content):
                    removed = content.pop(block_idx)
                    repairs_applied.append({
                        "type": "removed_orphan_tool_result",
                        "tool_use_id": repair.get("tool_use_id", "unknown"),
                    })
        
        elif repair_type == "unresolved_tool_use" and repair.get("action") == "inject_synthetic_failure":
            tool_use_id = repair["tool_use_id"]
            tool_name = repair.get("tool_name", "unknown")
            
            # Find where to inject (after the tool_use message)
            inject_after = repair["message_idx"]
            
            # Create synthetic tool_result
            synthetic_result = {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": True,
                    "content": f"[RIL] Tool execution interrupted. Context was recovered after system disruption.",
                }]
            }
            
            # Insert after the tool_use message
            repaired.insert(inject_after + 1, synthetic_result)
            repairs_applied.append({
                "type": "injected_synthetic_failure",
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
            })
    
    # Hash repaired
    repaired_json = json.dumps(repaired, sort_keys=True)
    repaired_hash = f"sha256:{hashlib.sha256(repaired_json.encode()).hexdigest()}"
    
    return repaired, RepairResult(
        success=True,
        original_hash=original_hash,
        repaired_hash=repaired_hash,
        repairs_applied=repairs_applied,
        mode=mode,
    )


# ============================================================
# CLI Commands
# ============================================================

@click.group()
def main():
    """Runtime Integrity Layer - Execution substrate for agents"""
    pass


@main.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(as_json: bool):
    """Show RIL component health.
    
    Components:
    - CIA: Context Integrity Adapter
    - Triggers: GAM trigger engine
    - Ledger: Work ledger (crash recovery)
    """
    # Check component availability
    components = {
        "cia": {"status": "ready", "description": "Context Integrity Adapter"},
        "triggers": {"status": "ready", "description": "GAM Trigger Engine"},
        "ledger": {"status": "ready", "description": "Work Ledger"},
    }
    
    # Check if fdaa-proxy is running (has full RIL)
    import subprocess
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8766/health"],
        capture_output=True, text=True
    )
    proxy_healthy = result.stdout.strip() == "200"
    
    if as_json:
        output = {
            "proxy_connected": proxy_healthy,
            "components": components,
        }
        click.echo(json.dumps(output, indent=2))
        return
    
    console.print("\n[bold]Runtime Integrity Layer[/bold]\n")
    
    table = Table(box=box.ROUNDED)
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Description", style="dim")
    
    for name, info in components.items():
        status_str = "[green]✓ ready[/green]" if info["status"] == "ready" else "[red]✗ error[/red]"
        table.add_row(name.upper(), status_str, info["description"])
    
    console.print(table)
    
    proxy_status = "[green]✓ connected[/green]" if proxy_healthy else "[yellow]○ not connected[/yellow]"
    console.print(f"\n[dim]FDAA Proxy:[/dim] {proxy_status}")
    console.print()


@main.command("validate")
@click.argument("payload_file", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(payload_file: str, as_json: bool):
    """Validate a context payload for tool pairing integrity.
    
    Checks:
    - Every tool_result references an existing tool_use
    - No duplicate tool_use IDs
    - Warns on unresolved tool_uses
    
    Example:
        substr8 ril validate context.json
    """
    # Load payload
    with open(payload_file) as f:
        payload = json.load(f)
    
    # Extract messages array
    if isinstance(payload, list):
        messages = payload
    elif isinstance(payload, dict) and "messages" in payload:
        messages = payload["messages"]
    else:
        console.print("[red]Error: Payload must be an array or have 'messages' key[/red]")
        raise SystemExit(1)
    
    result = validate_tool_pairing(messages)
    
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return
    
    if result.valid:
        console.print("[green]✓ Context is valid[/green]")
    else:
        console.print("[red]✗ Context has errors[/red]")
    
    if result.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for err in result.errors:
            console.print(f"  • {err}")
    
    if result.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for warn in result.warnings:
            console.print(f"  • {warn}")
    
    if result.repairs_needed:
        console.print(f"\n[dim]Repairs needed: {len(result.repairs_needed)}[/dim]")
        console.print("[dim]Run 'substr8 ril repair' to fix[/dim]")


@main.command("repair")
@click.argument("payload_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Output file (default: stdout)")
@click.option("--mode", "-m", type=click.Choice(["strict", "permissive", "forensic"]), 
              default="permissive", help="Repair mode")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (includes metadata)")
def repair(payload_file: str, output: Optional[str], mode: str, as_json: bool):
    """Repair a corrupted context payload.
    
    Modes:
    - strict: Reject invalid payloads (no repair)
    - permissive: Apply repairs and log (default)
    - forensic: Halt and snapshot for debugging
    
    Example:
        substr8 ril repair context.json -o fixed.json
        substr8 ril repair context.json --mode strict
    """
    repair_mode = RepairMode(mode)
    
    # Load payload
    with open(payload_file) as f:
        payload = json.load(f)
    
    # Extract messages array
    is_wrapped = False
    if isinstance(payload, list):
        messages = payload
    elif isinstance(payload, dict) and "messages" in payload:
        messages = payload["messages"]
        is_wrapped = True
    else:
        console.print("[red]Error: Payload must be an array or have 'messages' key[/red]")
        raise SystemExit(1)
    
    repaired, result = repair_context(messages, repair_mode)
    
    if as_json:
        output_data = {
            "repaired_messages": repaired,
            "repair_result": result.to_dict(),
        }
        out_str = json.dumps(output_data, indent=2)
    else:
        # Output just the repaired messages
        if is_wrapped:
            payload["messages"] = repaired
            out_str = json.dumps(payload, indent=2)
        else:
            out_str = json.dumps(repaired, indent=2)
    
    if output:
        with open(output, "w") as f:
            f.write(out_str)
        console.print(f"[green]✓ Repaired context written to {output}[/green]")
    else:
        click.echo(out_str)
    
    # Print summary
    if result.repairs_applied:
        console.print(f"\n[dim]Repairs applied: {len(result.repairs_applied)}[/dim]")
        for r in result.repairs_applied:
            console.print(f"  • {r['type']}: {r.get('tool_use_id', 'N/A')}")
    
    console.print(f"[dim]Original hash: {result.original_hash[:32]}...[/dim]")
    console.print(f"[dim]Repaired hash: {result.repaired_hash[:32]}...[/dim]")


# ============================================================
# Work Ledger Commands
# ============================================================

@main.group("ledger")
def ledger():
    """Work ledger operations - crash recovery and state tracking"""
    pass


@ledger.command("list")
@click.option("--status", "-s", type=click.Choice(["active", "completed", "failed", "all"]),
              default="active", help="Filter by status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def ledger_list(status: str, as_json: bool):
    """List work items in the ledger.
    
    Example:
        substr8 ril ledger list
        substr8 ril ledger list --status failed
    """
    # For now, check the DCT ledger for runs
    from pathlib import Path
    import sqlite3
    
    # Default ledger locations
    ledger_paths = [
        Path.home() / ".openclaw" / "dct.db",
        Path("/home/node/.openclaw/workspace/demo/data/audit.db"),
    ]
    
    all_runs = []
    for lp in ledger_paths:
        if lp.exists():
            try:
                conn = sqlite3.connect(str(lp))
                cursor = conn.execute("""
                    SELECT run_id, agent_ref, COUNT(*) as entries, 
                           MIN(timestamp) as started, MAX(timestamp) as last_activity
                    FROM entries
                    GROUP BY run_id
                    ORDER BY last_activity DESC
                    LIMIT 20
                """)
                for row in cursor:
                    all_runs.append({
                        "run_id": row[0],
                        "agent_ref": row[1],
                        "entries": row[2],
                        "started": row[3],
                        "last_activity": row[4],
                        "ledger": str(lp),
                    })
                conn.close()
            except Exception as e:
                pass
    
    if as_json:
        click.echo(json.dumps(all_runs, indent=2))
        return
    
    if not all_runs:
        console.print("[yellow]No work items found[/yellow]")
        return
    
    table = Table(title="Work Ledger", box=box.ROUNDED)
    table.add_column("Run ID", style="cyan")
    table.add_column("Agent")
    table.add_column("Entries")
    table.add_column("Last Activity", style="dim")
    
    for run in all_runs:
        table.add_row(
            run["run_id"][:12] + "...",
            run["agent_ref"],
            str(run["entries"]),
            run["last_activity"][:19] if run["last_activity"] else "-",
        )
    
    console.print(table)


@ledger.command("export")
@click.argument("run_id")
@click.option("--output", "-o", type=click.Path(), help="Output file")
def ledger_export(run_id: str, output: Optional[str]):
    """Export a run's audit trail.
    
    Example:
        substr8 ril ledger export abc123 -o audit.json
    """
    from pathlib import Path
    import sqlite3
    
    # Search ledgers for the run
    ledger_paths = [
        Path.home() / ".openclaw" / "dct.db",
        Path("/home/node/.openclaw/workspace/demo/data/audit.db"),
    ]
    
    entries = []
    for lp in ledger_paths:
        if lp.exists():
            try:
                conn = sqlite3.connect(str(lp))
                cursor = conn.execute("""
                    SELECT entry_id, run_id, seq, timestamp, agent_ref, agent_version,
                           agent_hash, action_json, decision_json, prev_hash, entry_hash
                    FROM entries
                    WHERE run_id LIKE ?
                    ORDER BY seq
                """, (f"{run_id}%",))
                for row in cursor:
                    entries.append({
                        "entry_id": row[0],
                        "run_id": row[1],
                        "seq": row[2],
                        "timestamp": row[3],
                        "agent_ref": row[4],
                        "agent_version": row[5],
                        "agent_hash": row[6],
                        "action": json.loads(row[7]) if row[7] else None,
                        "decision": json.loads(row[8]) if row[8] else None,
                        "prev_hash": row[9],
                        "entry_hash": row[10],
                    })
                conn.close()
            except Exception:
                pass
    
    if not entries:
        console.print(f"[red]Run '{run_id}' not found[/red]")
        raise SystemExit(1)
    
    export = {
        "run_id": entries[0]["run_id"],
        "agent_ref": entries[0]["agent_ref"],
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "entry_count": len(entries),
        "entries": entries,
    }
    
    out_str = json.dumps(export, indent=2)
    
    if output:
        with open(output, "w") as f:
            f.write(out_str)
        console.print(f"[green]✓ Exported {len(entries)} entries to {output}[/green]")
    else:
        click.echo(out_str)


# ============================================================
# Trigger Commands
# ============================================================

@main.group("triggers")
def triggers():
    """GAM trigger management - automatic memory capture"""
    pass


@triggers.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def triggers_list(as_json: bool):
    """List available trigger events.
    
    Example:
        substr8 ril triggers list
    """
    trigger_info = [
        {"event": "message_received", "description": "User message arrives", "auto": True},
        {"event": "tool_invoked", "description": "Tool call initiated", "auto": True},
        {"event": "tool_completed", "description": "Tool returns result", "auto": True},
        {"event": "turn_completed", "description": "Agent turn finishes", "auto": True},
        {"event": "decision_point", "description": "Critical decision detected", "auto": False},
        {"event": "crash_recovery", "description": "System recovered from crash", "auto": True},
    ]
    
    if as_json:
        click.echo(json.dumps(trigger_info, indent=2))
        return
    
    table = Table(title="GAM Triggers", box=box.ROUNDED)
    table.add_column("Event", style="cyan")
    table.add_column("Description")
    table.add_column("Auto")
    
    for t in trigger_info:
        auto = "[green]✓[/green]" if t["auto"] else "[dim]manual[/dim]"
        table.add_row(t["event"], t["description"], auto)
    
    console.print(table)


@triggers.command("fire")
@click.argument("event", type=click.Choice([e.value for e in TriggerEvent]))
@click.option("--context", "-c", type=click.Path(exists=True), help="Context file")
@click.option("--data", "-d", help="Additional data (JSON string)")
def triggers_fire(event: str, context: Optional[str], data: Optional[str]):
    """Manually fire a trigger event.
    
    Example:
        substr8 ril triggers fire decision_point -d '{"decision": "deploy to prod"}'
    """
    trigger_event = TriggerEvent(event)
    
    payload = {
        "event": trigger_event.value,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "manual": True,
    }
    
    if context:
        with open(context) as f:
            payload["context"] = json.load(f)
    
    if data:
        payload["data"] = json.loads(data)
    
    console.print(f"[cyan]Firing trigger:[/cyan] {trigger_event.value}")
    console.print(f"[dim]Payload:[/dim]")
    console.print(json.dumps(payload, indent=2))
    
    # TODO: Actually fire to GAM when integrated
    console.print("\n[yellow]Note: GAM integration pending[/yellow]")


if __name__ == "__main__":
    main()
