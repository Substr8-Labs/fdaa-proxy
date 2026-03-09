"""
Run Registry CLI Commands

Commands:
- substr8 registry runs     - List runs from registry
- substr8 registry inspect  - Inspect a run
- substr8 registry verify   - Verify a run from registry
- substr8 registry stats    - Show registry statistics
"""

import json
from datetime import datetime
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

REGISTRY_URL = "http://localhost:8098"


def get_registry_url():
    import os
    return os.environ.get("SUBSTR8_REGISTRY_URL", REGISTRY_URL)


@click.group()
def main():
    """Run Registry - Query and verify RunProofs"""
    pass


@main.command("runs")
@click.option("--agent", help="Filter by agent ID")
@click.option("--status", type=click.Choice(["active", "completed", "checkpoint", "failed"]))
@click.option("--since", help="Filter runs since date (ISO format)")
@click.option("--limit", default=20, help="Max runs to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_runs(agent: Optional[str], status: Optional[str], since: Optional[str], limit: int, as_json: bool):
    """List runs from the registry."""
    url = get_registry_url()
    params = {"limit": limit}
    
    if agent:
        params["agent_id"] = agent
    if status:
        params["status"] = status
    if since:
        params["since"] = since
    
    try:
        resp = httpx.get(f"{url}/v1/runs", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error: Cannot connect to Run Registry[/red]")
        console.print(f"[dim]URL: {url}[/dim]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)
    
    if as_json:
        print(json.dumps(data, indent=2))
        return
    
    runs = data.get("runs", [])
    
    if not runs:
        console.print("[dim]No runs found[/dim]")
        return
    
    table = Table(title="Runs", show_header=True)
    table.add_column("Run ID", style="cyan")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Events", justify="right")
    table.add_column("Started")
    
    for run in runs:
        status_style = {
            "completed": "green",
            "checkpoint": "yellow", 
            "failed": "red",
            "active": "blue"
        }.get(run.get("status", ""), "")
        
        table.add_row(
            run.get("run_id", "")[:20],
            run.get("agent_id", ""),
            f"[{status_style}]{run.get('status', '')}[/{status_style}]",
            str(run.get("event_count", 0)),
            run.get("started_at", "")[:19]
        )
    
    console.print(table)
    console.print(f"[dim]Total: {data.get('total', len(runs))} runs[/dim]")


@main.command("inspect")
@click.argument("run_id")
@click.option("--events", is_flag=True, help="Show event details")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def inspect(run_id: str, events: bool, as_json: bool):
    """Inspect a run from the registry."""
    url = get_registry_url()
    
    try:
        # Get timeline view
        resp = httpx.get(f"{url}/v1/runs/{run_id}/timeline", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Run not found: {run_id}[/red]")
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)
    except httpx.ConnectError:
        console.print("[red]Error: Cannot connect to Run Registry[/red]")
        raise SystemExit(1)
    
    if as_json:
        print(json.dumps(data, indent=2))
        return
    
    run = data.get("run", {})
    timeline = data.get("timeline", [])
    
    # Header
    console.print()
    console.print(Panel(
        f"[bold]{run.get('run_id', '')}[/bold]\n\n"
        f"Agent: {run.get('agent_id', '')}\n"
        f"Status: {run.get('status', '')}\n"
        f"Events: {run.get('event_count', 0)}\n"
        f"Started: {run.get('started_at', '')}\n"
        f"Ended: {run.get('ended_at', 'active')}\n"
        f"Root Hash: {run.get('root_hash', 'pending')[:32]}...",
        title="Run Details"
    ))
    
    # Timeline
    console.print("\n[bold]Timeline[/bold]\n")
    
    for i, event in enumerate(timeline):
        prefix = "├─" if i < len(timeline) - 1 else "└─"
        time = event.get("time", "")[:19]
        summary = event.get("summary", "")
        source = event.get("source", "")
        
        console.print(f"  {prefix} [dim]{time}[/dim]  {summary}")
        console.print(f"  │   [dim]{source}[/dim]")
    
    console.print()


@main.command("verify")
@click.argument("run_id")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed checks")
def verify(run_id: str, verbose: bool):
    """Verify a run from the registry."""
    url = get_registry_url()
    
    try:
        resp = httpx.post(f"{url}/v1/runs/{run_id}/verify", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Run not found: {run_id}[/red]")
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)
    except httpx.ConnectError:
        console.print("[red]Error: Cannot connect to Run Registry[/red]")
        raise SystemExit(1)
    
    verified = data.get("verified", False)
    checks = data.get("checks", {})
    errors = data.get("errors", [])
    root_hash = data.get("root_hash", "")
    
    console.print()
    
    if verified:
        console.print("[bold green]✓ VERIFIED[/bold green]")
    else:
        console.print("[bold red]✗ VERIFICATION FAILED[/bold red]")
    
    console.print()
    
    # Show checks
    for check, result in checks.items():
        icon = "✓" if result == "pass" else "✗" if result == "fail" else "?"
        color = "green" if result == "pass" else "red" if result == "fail" else "yellow"
        console.print(f"  [{color}]{icon}[/{color}] {check}: {result}")
    
    console.print()
    console.print(f"[dim]Run:[/dim]  {run_id}")
    console.print(f"[dim]Root:[/dim] {root_hash[:32]}..." if root_hash else "[dim]Root: pending[/dim]")
    
    if errors:
        console.print()
        console.print("[red]Errors:[/red]")
        for err in errors:
            console.print(f"  - {err}")
    
    console.print()


@main.command("stats")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def stats(as_json: bool):
    """Show registry statistics."""
    url = get_registry_url()
    
    try:
        resp = httpx.get(f"{url}/v1/stats", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error: Cannot connect to Run Registry[/red]")
        raise SystemExit(1)
    
    if as_json:
        print(json.dumps(data, indent=2))
        return
    
    console.print()
    console.print(Panel(
        f"[bold]Total Runs:[/bold] {data.get('total_runs', 0)}\n"
        f"[bold]Total Events:[/bold] {data.get('total_events', 0)}",
        title="Registry Statistics"
    ))
    
    # By status
    by_status = data.get("by_status", {})
    if by_status:
        console.print("\n[bold]By Status[/bold]")
        for status, count in by_status.items():
            console.print(f"  {status}: {count}")
    
    # By agent
    by_agent = data.get("by_agent", {})
    if by_agent:
        console.print("\n[bold]By Agent[/bold]")
        for agent, count in by_agent.items():
            console.print(f"  {agent}: {count}")
    
    # Recent runs
    recent = data.get("recent_runs", [])
    if recent:
        console.print("\n[bold]Recent Runs[/bold]")
        for run in recent[:5]:
            console.print(f"  {run.get('run_id', '')[:16]}  {run.get('status', '')}  {run.get('started_at', '')[:19]}")
    
    console.print()


@main.command("sync")
@click.option("--builder-url", default="http://localhost:8097", help="RunProof Builder URL")
@click.option("--limit", default=100, help="Max runs to sync")
def sync(builder_url: str, limit: int):
    """Sync runs from RunProof Builder to Registry."""
    registry_url = get_registry_url()
    
    console.print(f"[dim]Builder: {builder_url}[/dim]")
    console.print(f"[dim]Registry: {registry_url}[/dim]")
    console.print()
    
    try:
        # Get runs from builder
        resp = httpx.get(f"{builder_url}/v1/runs", params={"limit": limit}, timeout=10)
        resp.raise_for_status()
        runs = resp.json().get("runs", [])
    except httpx.ConnectError:
        console.print("[red]Error: Cannot connect to RunProof Builder[/red]")
        raise SystemExit(1)
    
    if not runs:
        console.print("[dim]No runs to sync[/dim]")
        return
    
    synced = 0
    skipped = 0
    failed = 0
    
    for run in runs:
        run_id = run.get("run_id")
        status = run.get("status")
        
        # Only sync completed/checkpoint runs
        if status not in ["completed", "checkpoint"]:
            skipped += 1
            continue
        
        try:
            # Get full proof
            proof_resp = httpx.get(f"{builder_url}/v1/runproof/{run_id}", timeout=10)
            if proof_resp.status_code != 200:
                skipped += 1
                continue
            
            proof = proof_resp.json()
            
            # Push to registry
            payload = {
                "run_id": run_id,
                "trace_id": proof.get("trace_id"),
                "agent_id": proof.get("agent_id"),
                "adapter": proof.get("adapter"),
                "status": proof.get("status"),
                "started_at": proof.get("started_at"),
                "ended_at": proof.get("ended_at"),
                "root_hash": proof.get("root_hash"),
                "runproof": proof
            }
            
            ingest_resp = httpx.post(f"{registry_url}/v1/ingest", json=payload, timeout=10)
            if ingest_resp.status_code == 200:
                synced += 1
                console.print(f"  [green]✓[/green] {run_id}")
            else:
                failed += 1
                console.print(f"  [red]✗[/red] {run_id}")
        except Exception as e:
            failed += 1
            console.print(f"  [red]✗[/red] {run_id}: {e}")
    
    console.print()
    console.print(f"[green]Synced: {synced}[/green]  [yellow]Skipped: {skipped}[/yellow]  [red]Failed: {failed}[/red]")


if __name__ == "__main__":
    main()
