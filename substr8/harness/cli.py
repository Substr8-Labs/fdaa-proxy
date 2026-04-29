"""
Substr8 Harness CLI — Validate, run, and inspect harness-core packages.

Bridges the CLI to harness-core's PackageLoader and PackageRunner.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

# Default harness-core root (can be overridden via env var)
HARNESS_CORE_ROOT = os.environ.get(
    "HARNESS_CORE_ROOT",
    os.path.expanduser("~/workspace/harnesses/harness-core")
)

HARNESS_CORE_PYTHON = os.environ.get(
    "HARNESS_CORE_PYTHON",
    os.path.join(HARNESS_CORE_ROOT, ".venv/bin/python3")
)


def _run_harness_script(script_name: str, args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run a harness-core script."""
    script_path = os.path.join(HARNESS_CORE_ROOT, "scripts", script_name)
    if not os.path.exists(script_path):
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        console.print(f"[dim]Set HARNESS_CORE_ROOT to your harness-core directory[/dim]")
        console.print(f"[dim]Current: {HARNESS_CORE_ROOT}[/dim]")
        sys.exit(1)
    
    cmd = [HARNESS_CORE_PYTHON, script_path] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if check and result.returncode != 0:
        console.print(f"[red]Error:[/red] {script_name} failed")
        if result.stderr:
            console.print(result.stderr)
        sys.exit(1)
    
    return result


@click.group()
def harness():
    """Harness package management — validate, run, inspect."""
    pass


@harness.command()
@click.argument("package_path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(package_path: str, as_json: bool):
    """Validate a harness package directory.
    
    Runs harness-core PackageLoader and reports validation results.
    
    Example:
        substr8 harness validate ./my-package
        substr8 harness validate ./my-package --json
    """
    result = _run_harness_script("validate_package.py", [package_path])
    
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        console.print(f"[red]Error:[/red] Could not parse output")
        console.print(result.stdout)
        sys.exit(1)
    
    if as_json:
        console.print_json(result.stdout)
        return
    
    valid = data.get("valid", False)
    package_id = data.get("package_id", "unknown")
    errors = data.get("errors", [])
    warnings = data.get("warnings", [])
    
    if valid:
        console.print()
        console.print(Panel(
            f"[bold green]✓ Package Valid[/bold green]\n\n"
            f"Package: {package_id}",
            title="Validation Result",
            border_style="green",
            box=box.ROUNDED
        ))
    else:
        console.print()
        console.print(Panel(
            f"[bold red]✗ Package Invalid[/bold red]\n\n"
            + "\n".join(f"  • {e}" for e in errors),
            title="Validation Failed",
            border_style="red",
            box=box.ROUNDED
        ))
    
    if warnings:
        console.print()
        console.print("[yellow]Warnings:[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")


@harness.command()
@click.argument("package_path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def run(package_path: str, as_json: bool):
    """Run a harness package through PackageRunner.
    
    Executes all phases and produces proof events and state proofs.
    
    Example:
        substr8 harness run ./my-package
        substr8 harness run ./my-package --json
    """
    result = _run_harness_script("run_package.py", [package_path])
    
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        console.print(f"[red]Error:[/red] Could not parse output")
        console.print(result.stdout)
        sys.exit(1)
    
    if as_json:
        console.print_json(result.stdout)
        return
    
    success = data.get("success", False)
    run_id = data.get("run_id", "unknown")
    playbook_id = data.get("playbook_id", "unknown")
    phase_count = data.get("phase_count", 0)
    proof_event_count = data.get("proof_event_count", 0)
    state_proof_count = data.get("state_proof_count", 0)
    errors = data.get("errors", [])
    threadhq_url = data.get("threadhq_url")
    
    status_icon = "✓" if success else "✗"
    status_color = "green" if success else "red"
    
    console.print()
    console.print(Panel(
        f"[bold {status_color}]{status_icon} Run {'Completed' if success else 'Failed'}[/bold {status_color}]",
        title=f"Run: {run_id}",
        border_style=status_color,
        box=box.ROUNDED
    ))
    console.print()
    
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Field", style="dim")
    table.add_column("Value")
    
    table.add_row("Run ID", run_id)
    table.add_row("Playbook", playbook_id)
    table.add_row("Phases", str(phase_count))
    table.add_row("Proof Events", str(proof_event_count))
    table.add_row("State Proofs", str(state_proof_count))
    
    console.print(table)
    
    if threadhq_url:
        console.print(f"\n[blue]🔗 View in ThreadHQ:[/blue] {threadhq_url}")
    
    if errors:
        console.print()
        console.print("[red]Errors:[/red]")
        for e in errors:
            console.print(f"  [red]•[/red] {e}")
    
    if not success:
        sys.exit(1)


@harness.command()
@click.argument("package_path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def inspect(package_path: str, as_json: bool):
    """Inspect a harness package manifest.
    
    Shows package ID, phases, tools, skills, and artifacts.
    
    Example:
        substr8 harness inspect ./my-package
    """
    # Read harness.package.yaml
    pkg_file = os.path.join(package_path, "harness.package.yaml")
    if not os.path.exists(pkg_file):
        console.print(f"[red]Error:[/red] No harness.package.yaml found in {package_path}")
        sys.exit(1)
    
    # Try to parse with PyYAML
    try:
        import yaml
        with open(pkg_file) as f:
            manifest = yaml.safe_load(f)
    except ImportError:
        # Fallback: just read and display
        with open(pkg_file) as f:
            console.print(f.read())
        return
    
    if as_json:
        console.print_json(json.dumps(manifest, indent=2, default=str))
        return
    
    console.print()
    console.print(Panel(
        f"[bold]{manifest.get('package_id', 'unknown')}[/bold]",
        title="Harness Package",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print()
    
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Field", style="dim")
    table.add_column("Value")
    
    table.add_row("Package ID", manifest.get("package_id", "-"))
    table.add_row("Version", str(manifest.get("version", "-")))
    table.add_row("Domain", manifest.get("domain", "-"))
    table.add_row("Description", manifest.get("description", "-"))
    
    phases = manifest.get("phases", [])
    table.add_row("Phases", str(len(phases)))
    
    for i, phase in enumerate(phases):
        table.add_row(f"  Phase {i+1}", phase.get("name", "-"))
    
    console.print(table)
    
    # Files
    console.print()
    console.print("[bold]Files:[/bold]")
    for root, dirs, files in os.walk(package_path):
        # Skip hidden dirs and common non-package dirs
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.startswith('.'):
                continue
            rel_path = os.path.relpath(os.path.join(root, f), package_path)
            console.print(f"  {rel_path}")


@harness.command("list")
@click.option("--limit", default=10, help="Number of recent runs to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_runs(limit: int, as_json: bool):
    """List recent harness runs.
    
    Requires ThreadHQ API to be running.
    
    Example:
        substr8 harness list
        substr8 harness list --limit 20
    """
    threadhq_url = os.environ.get("THREADHQ_API_URL", "http://localhost:8421")
    
    import httpx
    
    try:
        response = httpx.get(f"{threadhq_url}/api/graph/stats", timeout=5)
        response.raise_for_status()
        stats = response.json()
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot reach ThreadHQ at {threadhq_url}")
        console.print(f"[dim]{e}[/dim]")
        sys.exit(1)
    
    if as_json:
        console.print_json(json.dumps(stats, indent=2))
        return
    
    console.print()
    console.print(f"[bold]ThreadHQ Stats[/bold] (at {threadhq_url})")
    console.print(f"  Nodes: {stats.get('node_count', '?')}")
    console.print(f"  Edges: {stats.get('edge_count', '?')}")
    console.print()


if __name__ == "__main__":
    harness()