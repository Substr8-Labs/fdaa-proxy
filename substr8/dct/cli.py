"""
DCT CLI - Audit Ledger Commands

Commands for managing the tamper-evident audit ledger:
- append: Add an entry to the ledger
- verify: Verify chain integrity
- export: Export run data for audit
- list: List recent runs
- stats: Show ledger statistics
"""

import json
import sys
import uuid
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from ..schemas import DCTAction, DCTDecision, ActionType
from .ledger import DCTLedger, DEFAULT_LEDGER_PATH

console = Console()


@click.group()
def main():
    """DCT - Audit Ledger Commands
    
    Tamper-evident logging for agent actions.
    """
    pass


@main.command()
@click.option("--run-id", required=True, help="Run ID to append to")
@click.option("--agent-ref", required=True, help="Agent reference (namespace/name)")
@click.option("--agent-version", required=True, help="Agent version")
@click.option("--agent-hash", required=True, help="FDAA agent hash")
@click.option("--action-type", required=True, 
              type=click.Choice(["tool_call", "memory_read", "memory_write", "message_send", "agent_start", "agent_end", "error"]),
              help="Action type")
@click.option("--tool", help="Tool name (for tool_call)")
@click.option("--input-json", help="Action input as JSON")
@click.option("--output-json", help="Action output as JSON")
@click.option("--allowed/--denied", default=True, help="Whether action was allowed")
@click.option("--reason", default="", help="Decision reason")
@click.option("--policy-hash", help="Policy hash that made the decision")
@click.option("--memory-hash", help="GAM memory entry hash (if applicable)")
@click.option("--json", "output_json_flag", is_flag=True, help="Output as JSON")
def append(
    run_id: str,
    agent_ref: str,
    agent_version: str,
    agent_hash: str,
    action_type: str,
    tool: Optional[str],
    input_json: Optional[str],
    output_json: Optional[str],
    allowed: bool,
    reason: str,
    policy_hash: Optional[str],
    memory_hash: Optional[str],
    output_json_flag: bool,
):
    """Append an entry to the audit ledger.
    
    Example:
        substr8 dct append --run-id run-123 --agent-ref substr8/analyst \\
            --agent-version 1.0.0 --agent-hash sha256:abc... \\
            --action-type tool_call --tool web_search \\
            --input-json '{"query": "AI governance"}' \\
            --allowed --reason "tool in capabilities.allow"
    """
    action = DCTAction(
        type=ActionType(action_type),
        tool=tool,
        input=json.loads(input_json) if input_json else None,
        output=json.loads(output_json) if output_json else None,
    )
    
    decision = DCTDecision(
        allowed=allowed,
        reason=reason or ("Allowed" if allowed else "Denied"),
        policy_hash=policy_hash,
    )
    
    with DCTLedger() as ledger:
        entry = ledger.append(
            run_id=run_id,
            agent_ref=agent_ref,
            agent_version=agent_version,
            agent_hash=agent_hash,
            action=action,
            decision=decision,
            memory_entry_hash=memory_hash,
        )
    
    if output_json_flag:
        console.print(json.dumps(entry.to_dict(), indent=2))
    else:
        status = "✓" if allowed else "✗"
        color = "green" if allowed else "red"
        console.print(f"\n[{color}]{status} Entry appended[/{color}]")
        console.print(f"  Entry ID:   {entry.entry_id}")
        console.print(f"  Run:        {run_id} (seq={entry.seq})")
        console.print(f"  Entry Hash: {entry.entry_hash}")
        console.print(f"  Prev Hash:  {entry.prev_hash[:40]}...")


@main.command()
@click.option("--run-id", help="Verify specific run")
@click.option("--all", "verify_all", is_flag=True, help="Verify all runs")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def verify(run_id: Optional[str], verify_all: bool, output_json: bool):
    """Verify chain integrity of the ledger.
    
    Examples:
        substr8 dct verify --run-id run-123
        substr8 dct verify --all
    """
    if not run_id and not verify_all:
        console.print("[red]Error:[/red] Specify --run-id or --all")
        sys.exit(1)
    
    with DCTLedger() as ledger:
        if verify_all:
            result = ledger.verify_all()
        else:
            result = ledger.verify_run(run_id)
    
    if output_json:
        console.print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("verified") else 1)
    
    if verify_all:
        status = "✓" if result["verified"] else "✗"
        color = "green" if result["verified"] else "red"
        console.print(f"\n[{color}]{status} Ledger verification[/{color}]")
        console.print(f"  Runs checked:     {result['runs_checked']}")
        console.print(f"  Runs with errors: {result['runs_with_errors']}")
        console.print(f"  Total errors:     {result['total_errors']}")
        
        if not result["verified"]:
            for run_result in result["results"]:
                if not run_result["verified"]:
                    console.print(f"\n  [red]Run {run_result['run_id']}:[/red]")
                    for error in run_result.get("errors", []):
                        console.print(f"    • {error}")
    else:
        status = "✓" if result["verified"] else "✗"
        color = "green" if result["verified"] else "red"
        console.print(f"\n[{color}]{status} Run verification: {run_id}[/{color}]")
        console.print(f"  Entries: {result.get('entry_count', 0)}")
        
        if not result["verified"]:
            for error in result.get("errors", []):
                console.print(f"  [red]• {error}[/red]")
    
    sys.exit(0 if result.get("verified") else 1)


@main.command("export")
@click.option("--run-id", help="Export specific run")
@click.option("--output", "-o", help="Output file (default: stdout)")
@click.option("--format", "fmt", type=click.Choice(["json", "jsonl"]), default="json")
def export_cmd(run_id: Optional[str], output: Optional[str], fmt: str):
    """Export ledger data for audit.
    
    Examples:
        substr8 dct export --run-id run-123 -o audit.json
        substr8 dct export --format jsonl -o all_runs.jsonl
    """
    with DCTLedger() as ledger:
        if run_id:
            data = ledger.export_run(run_id)
            if "error" in data:
                console.print(f"[red]Error:[/red] {data['error']}")
                sys.exit(1)
            
            json_output = json.dumps(data, indent=2)
        else:
            # Export all runs
            if fmt == "jsonl":
                lines = [json.dumps(run) for run in ledger.export_all()]
                json_output = "\n".join(lines)
            else:
                runs = list(ledger.export_all())
                json_output = json.dumps({"runs": runs}, indent=2)
    
    if output:
        Path(output).write_text(json_output)
        console.print(f"[green]✓[/green] Exported to {output}")
    else:
        console.print(json_output)


@main.command("list")
@click.option("--limit", default=20, help="Number of runs to show")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def list_runs(limit: int, output_json: bool):
    """List recent runs in the ledger.
    
    Example:
        substr8 dct list --limit 10
    """
    with DCTLedger() as ledger:
        runs = ledger.list_runs(limit=limit)
    
    if output_json:
        console.print(json.dumps(runs, indent=2))
        return
    
    if not runs:
        console.print("\n[dim]No runs found.[/dim]\n")
        return
    
    table = Table(title=f"Recent Runs ({len(runs)})")
    table.add_column("Run ID", style="cyan")
    table.add_column("Agent", style="green")
    table.add_column("Entries")
    table.add_column("Started")
    
    for run in runs:
        table.add_row(
            run["run_id"][:16] + "...",
            run["agent_ref"],
            str(run["entry_count"]),
            run["started_at"][:19],
        )
    
    console.print()
    console.print(table)
    console.print()


@main.command()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def stats(output_json: bool):
    """Show ledger statistics.
    
    Example:
        substr8 dct stats
    """
    with DCTLedger() as ledger:
        data = ledger.stats()
    
    if output_json:
        console.print(json.dumps(data, indent=2))
        return
    
    console.print("\n[bold]DCT Ledger Statistics[/bold]\n")
    console.print(f"  Total entries:   {data['total_entries']}")
    console.print(f"  Total runs:      {data['total_runs']}")
    console.print(f"  Unique agents:   {data['unique_agents']}")
    console.print(f"  Earliest entry:  {data['earliest_entry'] or 'N/A'}")
    console.print(f"  Latest entry:    {data['latest_entry'] or 'N/A'}")
    console.print(f"  Ledger path:     {data['ledger_path']}")
    
    if data["action_breakdown"]:
        console.print("\n[bold]Action Types[/bold]")
        for action_type, count in data["action_breakdown"].items():
            console.print(f"  {action_type}: {count}")
    
    if data["decision_breakdown"]:
        console.print("\n[bold]Decisions[/bold]")
        for decision, count in data["decision_breakdown"].items():
            color = "green" if decision == "allowed" else "red"
            console.print(f"  [{color}]{decision}[/{color}]: {count}")
    
    console.print()


@main.command("new-run")
@click.option("--prefix", default="run", help="Run ID prefix")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def new_run(prefix: str, output_json: bool):
    """Generate a new run ID.
    
    Example:
        substr8 dct new-run
        run_id=$(substr8 dct new-run --json | jq -r .run_id)
    """
    run_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
    
    if output_json:
        console.print(json.dumps({"run_id": run_id}))
    else:
        console.print(f"\n[green]✓[/green] New run ID: [bold]{run_id}[/bold]\n")


if __name__ == "__main__":
    main()
