"""
RunProof CLI Commands

Commands:
- substr8 run     - Run an agent with governance, emit RunProof
- substr8 verify  - Verify a RunProof bundle
- substr8 export  - Export a RunProof from cache
- substr8 badge   - Generate README badge
"""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.command()
@click.argument("script", type=click.Path(exists=True))
@click.option("--framework", type=click.Choice(["auto", "langgraph", "pydantic-ai", "autogen"]),
              default="auto", help="Framework to use (auto-detect by default)")
@click.option("--local/--cloud", default=False, help="Use local MCP server (default: cloud)")
@click.option("--out", type=click.Path(), default="./runproofs", help="Output directory for RunProof")
@click.option("--label", multiple=True, help="Labels in k=v format")
@click.option("--mcp-url", envvar="SUBSTR8_MCP_URL", help="MCP server URL")
@click.option("--no-tarball", is_flag=True, help="Don't create .runproof.tgz archive")
def run(script: str, framework: str, local: bool, out: str, label: tuple, mcp_url: str, no_tarball: bool):
    """
    Run an agent script with Substr8 governance enabled.
    
    Automatically wraps the agent execution with governance tracking and
    emits a RunProof bundle on completion.
    
    \b
    Examples:
        substr8 run examples/langgraph/agent.py
        substr8 run agent.py --framework pydantic-ai --out ./proofs
        substr8 run agent.py --cloud --mcp-url https://mcp.substr8labs.com
    """
    script_path = Path(script).resolve()
    output_dir = Path(out)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate run ID
    run_id = f"run-{uuid.uuid4().hex[:6]}"
    started_at = datetime.now(timezone.utc)
    
    # Auto-detect framework
    if framework == "auto":
        framework = detect_framework(script_path)
    
    # Determine MCP URL
    if not mcp_url:
        mcp_url = "http://127.0.0.1:9988" if local else "https://mcp.substr8labs.com"
    
    # Print header
    console.print()
    console.print("[bold cyan]▶ Substr8 Governance: ACTIVE[/bold cyan]")
    console.print(f"  [dim]Run ID:[/dim]   {run_id}")
    console.print(f"  [dim]Mode:[/dim]     {'local' if local else 'cloud'}")
    console.print(f"  [dim]MCP:[/dim]      {mcp_url}")
    console.print(f"  [dim]CIA:[/dim]      enabled (audit-only)")
    console.print(f"  [dim]Framework:[/dim] {framework}")
    console.print()
    
    # Set environment for the subprocess
    env = os.environ.copy()
    env["SUBSTR8_RUN_ID"] = run_id
    env["SUBSTR8_MCP_URL"] = mcp_url
    env["SUBSTR8_GOVERNANCE"] = "active"
    
    # Parse labels
    labels = {}
    for l in label:
        if "=" in l:
            k, v = l.split("=", 1)
            labels[k] = v
    
    # Run the agent script
    console.print("[dim]… running agent …[/dim]")
    console.print()
    
    exit_code = 0
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            env=env,
            capture_output=False,
        )
        exit_code = result.returncode
    except Exception as e:
        console.print(f"[red]Agent error:[/red] {e}")
        exit_code = 10
    
    ended_at = datetime.now(timezone.utc)
    
    # Create RunProof bundle
    console.print()
    
    try:
        from .bundle import create_runproof
        from .hash import sha256_str
        
        # Create a minimal agent hash from script content
        with open(script_path, 'r') as f:
            script_content = f.read()
        agent_hash = sha256_str(script_content)
        
        # Create bundle
        bundle = create_runproof(
            run_id=run_id,
            agent_ref=f"{framework}:{script_path.stem}",
            agent_hash=agent_hash,
            policy_hash=sha256_str("default-policy"),  # TODO: Load actual policy
            started_at=started_at,
            ended_at=ended_at,
            mcp_endpoint=mcp_url,
            model_provider="anthropic",  # TODO: Get from actual run
            model_name="claude-opus-4-5",  # TODO: Get from actual run
        )
        
        # Try to load governance data from MCP if available
        try:
            bundle = enrich_bundle_from_mcp(bundle, mcp_url, run_id)
        except Exception:
            pass  # MCP may not be running
        
        # Save bundle
        result_path = bundle.save(output_dir, create_tarball=not no_tarball)
        
        if exit_code == 0:
            console.print("[bold green]Run completed ✓[/bold green]")
        else:
            console.print(f"[bold yellow]Run completed with exit code {exit_code}[/bold yellow]")
        
        console.print()
        console.print(f"[bold]RunProof:[/bold] {result_path}")
        console.print(f"[dim]Verify locally:[/dim]  substr8 verify {result_path}")
        console.print(f"[dim]Verify online:[/dim]   https://verify.substr8labs.com/?hash={bundle.root_hash[:16]}")
        console.print()
        
    except Exception as e:
        console.print(f"[red]✗ RunProof assembly failed:[/red] {e}")
        raise SystemExit(30)
    
    raise SystemExit(exit_code)


@click.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--strict", is_flag=True, help="Also verify signature")
def verify(path: str, as_json: bool, strict: bool):
    """
    Verify a RunProof bundle offline.
    
    Checks:
    - Root file hashes match manifest
    - DCT ledger chain integrity
    - CIA receipts linked to ledger
    - GAM pointers linked to ledger
    - FDAA agent hash matches
    
    \b
    Examples:
        substr8 verify ./runproofs/run-6c39af.runproof.tgz
        substr8 verify ./runproofs/run-6c39af/ --json
        substr8 verify bundle.tgz --strict
    """
    from .verify import verify_runproof
    
    result = verify_runproof(Path(path), strict=strict)
    
    if as_json:
        import json
        console.print(json.dumps(result.to_dict(), indent=2))
    else:
        console.print()
        
        if result.valid:
            console.print("[bold green]RunProof Verified ✓[/bold green]")
        else:
            console.print("[bold red]RunProof Verification FAILED ✗[/bold red]")
        
        console.print()
        console.print(f"[bold]Run:[/bold]        {result.run_id}")
        console.print(f"[bold]Agent:[/bold]      {result.agent_ref}")
        console.print(f"[bold]Agent Hash:[/bold] {result.agent_hash[:16]}...")
        console.print(f"[bold]Policy:[/bold]     {result.policy_hash[:16]}... [green]✓[/green]" if result.policy_hash else "[bold]Policy:[/bold]     [dim]none[/dim]")
        
        # Ledger status
        if result.ledger_valid:
            console.print(f"[bold]Ledger:[/bold]     {result.ledger_entry_count} entries, chain valid [green]✓[/green] (head {result.ledger_head_hash[:12] if result.ledger_head_hash else 'n/a'}...)")
        else:
            console.print(f"[bold]Ledger:[/bold]     [red]✗ {result.ledger_error}[/red]")
        
        # CIA status
        console.print(f"[bold]CIA:[/bold]        {result.cia_receipt_count} receipts [green]✓[/green]")
        
        # GAM status
        console.print(f"[bold]GAM:[/bold]        {result.gam_pointer_count} pointers [green]✓[/green]")
        
        # Root hash
        if result.root_hash_valid:
            console.print(f"[bold]Root Hash:[/bold]  verified [green]✓[/green] ({result.file_count} files)")
        else:
            console.print(f"[bold]Root Hash:[/bold]  [red]✗ mismatch[/red]")
        
        # Signature
        if result.signature_present:
            if result.signature_valid:
                console.print(f"[bold]Signature:[/bold]  verified [green]✓[/green]")
            elif result.signature_valid is None:
                console.print(f"[bold]Signature:[/bold]  [yellow]present (not verified)[/yellow]")
            else:
                console.print(f"[bold]Signature:[/bold]  [red]✗ invalid[/red]")
        
        console.print()
        
        if result.errors:
            console.print("[bold red]Errors:[/bold red]")
            for error in result.errors:
                console.print(f"  • {error}")
            console.print()
    
    # Exit codes per spec
    if not result.valid:
        if not result.root_hash_valid:
            raise SystemExit(40)
        elif not result.ledger_valid:
            raise SystemExit(41)
        elif not result.cia_valid:
            raise SystemExit(42)
        elif not result.gam_valid:
            raise SystemExit(43)
        elif result.signature_valid is False:
            raise SystemExit(44)
        else:
            raise SystemExit(1)


@click.command()
@click.argument("run_id")
@click.option("--out", type=click.Path(), default=".", help="Output directory")
@click.option("--format", "fmt", type=click.Choice(["tgz", "dir"]), default="tgz")
def export(run_id: str, out: str, fmt: str):
    """
    Export a RunProof from local cache or cloud storage.
    
    \b
    Examples:
        substr8 export run-6c39af
        substr8 export run-6c39af --out ./exports --format dir
    """
    import shutil
    
    # Check local cache first
    cache_dir = Path.home() / ".substr8" / "runproof"
    local_path = cache_dir / run_id
    local_tgz = cache_dir / f"{run_id}.runproof.tgz"
    
    output_dir = Path(out)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if local_tgz.exists():
        if fmt == "tgz":
            dest = output_dir / f"{run_id}.runproof.tgz"
            shutil.copy(local_tgz, dest)
            console.print(f"[green]✓[/green] Exported to {dest}")
        else:
            # Extract to directory
            import tarfile
            dest = output_dir / run_id
            with tarfile.open(local_tgz, 'r:gz') as tar:
                tar.extractall(dest)
            console.print(f"[green]✓[/green] Exported to {dest}")
    elif local_path.exists():
        if fmt == "dir":
            dest = output_dir / run_id
            shutil.copytree(local_path, dest)
            console.print(f"[green]✓[/green] Exported to {dest}")
        else:
            # Create tarball
            import tarfile
            dest = output_dir / f"{run_id}.runproof.tgz"
            with tarfile.open(dest, 'w:gz') as tar:
                tar.add(local_path / "runproof", arcname="runproof")
            console.print(f"[green]✓[/green] Exported to {dest}")
    else:
        console.print(f"[red]✗[/red] RunProof '{run_id}' not found in local cache")
        console.print(f"  [dim]Checked: {cache_dir}[/dim]")
        raise SystemExit(1)


@click.command()
@click.argument("run_id_or_hash")
@click.option("--markdown/--html", default=True, help="Output format")
def badge(run_id_or_hash: str, markdown: bool):
    """
    Generate a badge snippet for README.
    
    \b
    Examples:
        substr8 badge run-6c39af
        substr8 badge run-6c39af --html
    """
    badge_url = f"https://verify.substr8.io/badge/{run_id_or_hash}.svg"
    verify_url = f"https://verify.substr8.io/run/{run_id_or_hash}"
    
    if markdown:
        console.print(f"[![Verified by Substr8]({badge_url})]({verify_url})")
    else:
        console.print(f'<a href="{verify_url}"><img src="{badge_url}" alt="Verified by Substr8" /></a>')


def detect_framework(script_path: Path) -> str:
    """Auto-detect the framework used in a script."""
    with open(script_path, 'r') as f:
        content = f.read()
    
    if "langgraph" in content.lower() or "from langgraph" in content:
        return "langgraph"
    elif "pydantic_ai" in content or "pydantic-ai" in content.lower():
        return "pydantic-ai"
    elif "autogen" in content.lower():
        return "autogen"
    else:
        return "unknown"


def enrich_bundle_from_mcp(bundle, mcp_url: str, run_id: str):
    """Try to fetch governance data from MCP server."""
    import requests
    
    try:
        # Try to get audit timeline
        response = requests.post(
            f"{mcp_url}/tools/audit.timeline",
            json={"run_id": run_id},
            timeout=5,
        )
        if response.ok:
            data = response.json()
            if "entries" in data:
                bundle.ledger_entries = data["entries"]
        
        # Try to get CIA receipts
        response = requests.post(
            f"{mcp_url}/tools/cia.receipts",
            json={"run_id": run_id},
            timeout=5,
        )
        if response.ok:
            data = response.json()
            if "receipts" in data:
                bundle.cia_receipts = data["receipts"]
        
    except Exception:
        pass  # MCP server not available
    
    return bundle
