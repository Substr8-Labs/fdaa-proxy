"""
Substr8 ThreadHQ CLI — Run graph visualization and inspection.

Bridges the CLI to ThreadHQ API.
"""

import json
import os
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

THREADHQ_URL = os.environ.get("THREADHQ_API_URL", "http://localhost:8421")


@click.group()
def threadhq():
    """ThreadHQ — run graph visualization and inspection."""
    pass


@threadhq.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(as_json: bool):
    """Check ThreadHQ API health.
    
    Example:
        substr8 threadhq status
    """
    import httpx
    
    try:
        response = httpx.get(f"{THREADHQ_URL}/health", timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot reach ThreadHQ at {THREADHQ_URL}")
        console.print(f"[dim]{e}[/dim]")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    console.print()
    console.print(Panel(
        f"[bold green]✓ ThreadHQ Healthy[/bold green]\n\n"
        f"URL: {THREADHQ_URL}\n"
        f"Status: {data.get('status', 'unknown')}",
        title="ThreadHQ Status",
        border_style="green",
        box=box.ROUNDED
    ))
    console.print()


@threadhq.command()
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def run(run_id: str, as_json: bool):
    """View a specific run graph.
    
    Example:
        substr8 threadhq run run-ndis-plan-review-1234567890
    """
    import httpx
    
    try:
        response = httpx.get(f"{THREADHQ_URL}/api/graph/run/{run_id}", timeout=10)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Error:[/red] Run not found: {run_id}")
        else:
            console.print(f"[red]Error:[/red] ThreadHQ returned {e.response.status_code}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot reach ThreadHQ at {THREADHQ_URL}")
        console.print(f"[dim]{e}[/dim]")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    console.print()
    console.print(Panel(
        f"[bold]{run_id}[/bold]",
        title="ThreadHQ Run",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()
    
    nodes = data.get("nodes", data.get("vertices", []))
    edges = data.get("edges", data.get("relationships", []))
    
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Property", style="dim")
    table.add_column("Value")
    
    table.add_row("Run ID", run_id)
    table.add_row("Nodes", str(len(nodes)))
    table.add_row("Edges", str(len(edges)))
    table.add_row("ThreadHQ", f"{THREADHQ_URL}/runs/{run_id}")
    
    console.print(table)
    console.print()
    console.print(f"[blue]🔗 Open in browser:[/blue] {THREADHQ_URL}/runs/{run_id}")


@threadhq.command("list")
@click.option("--limit", default=10, help="Number of recent runs")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_runs(limit: int, as_json: bool):
    """List recent runs.
    
    Example:
        substr8 threadhq list
        substr8 threadhq list --limit 20
    """
    import httpx
    
    try:
        response = httpx.get(f"{THREADHQ_URL}/api/graph/stats", timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot reach ThreadHQ at {THREADHQ_URL}")
        console.print(f"[dim]{e}[/dim]")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    console.print()
    console.print(f"[bold]ThreadHQ[/bold] (at {THREADHQ_URL})")
    console.print(f"  Nodes: {data.get('node_count', '?')}")
    console.print(f"  Edges: {data.get('edge_count', '?')}")
    console.print()


@threadhq.command()
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def graph(run_id: str, as_json: bool):
    """Show run graph structure.
    
    Displays nodes and edges for a specific run.
    
    Example:
        substr8 threadhq graph run-ndis-plan-review-1234567890
    """
    import httpx
    
    try:
        response = httpx.get(f"{THREADHQ_URL}/api/graph/run/{run_id}", timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot reach ThreadHQ")
        console.print(f"[dim]{e}[/dim]")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    nodes = data.get("nodes", data.get("vertices", []))
    edges = data.get("edges", data.get("relationships", []))
    
    console.print()
    console.print(f"[bold]Graph for {run_id}[/bold]")
    console.print()
    
    # Nodes table
    if nodes:
        node_table = Table(title="Nodes", box=box.ROUNDED)
        node_table.add_column("ID", style="dim", width=30)
        node_table.add_column("Type", style="cyan")
        node_table.add_column("Label", width=40)
        
        for node in nodes[:20]:  # Limit display
            node_id = node.get("id", node.get("node_id", "?"))
            node_type = node.get("type", node.get("node_type", "?"))
            label = node.get("label", node.get("name", ""))
            node_table.add_row(str(node_id)[:30], node_type, label[:40])
        
        console.print(node_table)
        if len(nodes) > 20:
            console.print(f"  [dim]... and {len(nodes) - 20} more[/dim]")
    
    # Edges table
    if edges:
        edge_table = Table(title="Edges", box=box.ROUNDED)
        edge_table.add_column("From", style="dim", width=20)
        edge_table.add_column("Type", style="cyan")
        edge_table.add_column("To", style="dim", width=20)
        
        for edge in edges[:20]:
            source = edge.get("source", edge.get("from", "?"))
            edge_type = edge.get("type", edge.get("relationship", "?"))
            target = edge.get("target", edge.get("to", "?"))
            edge_table.add_row(str(source)[:20], edge_type, str(target)[:20])
        
        console.print(edge_table)
        if len(edges) > 20:
            console.print(f"  [dim]... and {len(edges) - 20} more[/dim]")
    
    console.print()


if __name__ == "__main__":
    threadhq()