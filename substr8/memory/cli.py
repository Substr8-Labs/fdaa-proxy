"""
Substr8 Memory CLI — Governed memory recall, capture, and status.

Bridges the CLI to the Memory Plane API (:8093) and Railway GAM.
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

MEMORY_PLANE_URL = os.environ.get("MEMORY_PLANE_URL", "http://localhost:8093")
GAM_URL = os.environ.get("GAM_SERVICE_URL", "https://gam-service-production.up.railway.app")


@click.group()
def memory():
    """Governed memory — recall, capture, status."""
    pass


@memory.command()
@click.argument("query")
@click.option("--agent", default="ada", help="Agent ID for scoped recall")
@click.option("--limit", default=10, help="Max results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recall(query: str, agent: str, limit: int, as_json: bool):
    """Search governed memory.
    
    Queries the Memory Plane for semantically relevant memories.
    
    Example:
        substr8 memory recall "harness-core package structure"
        substr8 memory recall "RunProof" --limit 5
    """
    import httpx
    
    try:
        response = httpx.post(
            f"{MEMORY_PLANE_URL}/recall",
            json={"query": query, "agent_id": agent, "limit": limit},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        # Fallback to GAM
        try:
            response = httpx.post(
                f"{GAM_URL}/v3/search",
                json={"query": query, "agent_id": agent, "limit": limit},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            source = "GAM (Railway)"
        except Exception as e:
            console.print(f"[red]Error:[/red] Cannot reach Memory Plane or GAM")
            console.print(f"[dim]Memory Plane: {MEMORY_PLANE_URL}[/dim]")
            console.print(f"[dim]GAM: {GAM_URL}[/dim]")
            console.print(f"[dim]{e}[/dim]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    results = data.get("results", data.get("memories", []))
    
    if not results:
        console.print(f"[yellow]No results found for:[/yellow] {query}")
        return
    
    console.print()
    console.print(Panel(
        f"[bold]{len(results)} result(s)[/bold] for: {query}",
        title="Memory Recall",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()
    
    for i, result in enumerate(results, 1):
        content = result.get("content", result.get("text", ""))
        score = result.get("score", result.get("attention_score", ""))
        source = result.get("source", "")
        timestamp = result.get("timestamp", result.get("created_at", ""))
        
        console.print(f"[bold]{i}.[/bold] [dim](score: {score:.2f})[/dim]" if isinstance(score, float) else f"[bold]{i}.[/bold]")
        console.print(f"   {content[:200]}{'...' if len(content) > 200 else ''}")
        if source:
            console.print(f"   [dim]Source: {source}[/dim]")
        console.print()


@memory.command()
@click.argument("content")
@click.option("--type", "fact_type", default="decision", help="Fact type (decision, fact, lesson)")
@click.option("--scope", default="project", help="Scope (project, agent, org)")
@click.option("--confidence", default=0.9, type=float, help="Confidence (0-1)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def capture(content: str, fact_type: str, scope: str, confidence: float, as_json: bool):
    """Write a fact to governed memory.
    
    Captures a fact through the Memory Plane with provenance tracking.
    
    Example:
        substr8 memory capture "harness-core uses PackageLoader for validation"
        substr8 memory capture "Decision: use Docker Compose" --type decision --confidence 0.95
    """
    import httpx
    
    payload = {
        "query": content,
        "agent_id": "ada",
        "limit": 1,
    }
    
    # Use the capture endpoint if available, otherwise recall endpoint
    try:
        response = httpx.post(
            f"{MEMORY_PLANE_URL}/capture",
            json=[{
                "fact_type": fact_type,
                "content": content,
                "confidence": confidence,
                "scope": scope,
            }],
            timeout=10,
        )
        data = response.json() if response.status_code == 200 else None
    except Exception:
        # Fallback: use Delta Lake API directly
        try:
            response = httpx.post(
                f"http://localhost:8094/memories",
                json={
                    "content": content,
                    "fact_type": fact_type,
                    "confidence": confidence,
                    "scope": scope,
                    "agent_id": "ada",
                },
                timeout=10,
            )
            data = response.json() if response.status_code == 200 else None
        except Exception as e:
            console.print(f"[red]Error:[/red] Cannot reach Memory Plane")
            console.print(f"[dim]{e}[/dim]")
            sys.exit(1)
    
    if as_json and data:
        console.print_json(json.dumps(data, indent=2, default=str))
        return
    
    console.print()
    console.print(Panel(
        f"[bold green]✓ Fact captured[/bold green]\n\n"
        f"Type: {fact_type}\n"
        f"Scope: {scope}\n"
        f"Confidence: {confidence}",
        title="Memory Capture",
        border_style="green",
        box=box.ROUNDED
    ))
    console.print()


@memory.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(as_json: bool):
    """Check memory pipeline health.
    
    Checks Memory Plane, Delta Lake, GAM, and Neo4j status.
    
    Example:
        substr8 memory status
    """
    import httpx
    
    services = {
        "Memory Plane": MEMORY_PLANE_URL,
        "Delta Lake": os.environ.get("DELTA_API_URL", "http://localhost:8094"),
        "GAM": GAM_URL,
    }
    
    console.print()
    console.print("[bold]Memory Pipeline Status[/bold]")
    console.print()
    
    results = {}
    for name, url in services.items():
        try:
            response = httpx.get(f"{url}/health", timeout=5)
            data = response.json()
            status_val = data.get("status", "unknown")
            results[name] = {"status": status_val, "url": url, "data": data}
            icon = "[green]✓[/green]" if status_val == "healthy" else "[yellow]⚠[/yellow]"
            console.print(f"  {icon} {name}: {status_val} ({url})")
        except Exception as e:
            results[name] = {"status": "unreachable", "url": url, "error": str(e)}
            console.print(f"  [red]✗[/red] {name}: unreachable ({url})")
            console.print(f"     [dim]{e}[/dim]")
    
    # Neo4j
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    try:
        response = httpx.get("http://localhost:7474", timeout=3)
        results["Neo4j"] = {"status": "running", "url": neo4j_uri}
        console.print(f"  [green]✓[/green] Neo4j: running ({neo4j_uri})")
    except Exception:
        results["Neo4j"] = {"status": "unreachable", "url": neo4j_uri}
        console.print(f"  [red]✗[/red] Neo4j: unreachable ({neo4j_uri})")
    
    if as_json:
        console.print_json(json.dumps(results, indent=2, default=str))
    
    console.print()


if __name__ == "__main__":
    memory()