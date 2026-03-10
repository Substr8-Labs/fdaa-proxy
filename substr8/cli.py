"""
Substr8 CLI - Unified command-line interface

Usage:
    substr8 gam <command>      Git-Native Agent Memory
    substr8 fdaa <command>     File-Driven Agent Architecture (planned)
    substr8 acc <command>      Agent Capability Control (planned)
"""

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(version="1.7.1", prog_name="substr8")
def main():
    """Substr8 Platform CLI - Verifiable AI Infrastructure"""
    pass


# === GAM Integration ===

from substr8.gam.cli import main as gam_main

# Register GAM as a subcommand group
main.add_command(gam_main, name="gam")


# === FDAA Integration ===

from substr8.fdaa.cli import main as fdaa_main

# Register FDAA as a subcommand group
main.add_command(fdaa_main, name="fdaa")


# === Agent Identity ===

from substr8.agent.cli import agent as agent_main

# Register agent as a subcommand group
main.add_command(agent_main, name="agent")


# === ACC (Agent Capability Control) ===

@main.group()
def acc():
    """Agent Capability Control - Runtime capability enforcement"""
    pass


@acc.command("check")
@click.argument("agent_id")
@click.argument("tool")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def acc_check(agent_id: str, tool: str, as_json: bool):
    """Check if an agent is allowed to use a tool.
    
    Example:
        substr8 acc check analyst web_search
        substr8 acc check analyst shell_exec --json
    """
    from substr8.acc import check
    
    result = check(agent_id, tool)
    
    if as_json:
        import json
        console.print(json.dumps(result.to_dict(), indent=2))
    else:
        if result.allowed:
            console.print(f"[green]✓ ALLOWED[/green] {tool}")
        else:
            console.print(f"[red]✗ DENIED[/red] {tool}")
        console.print(f"  [dim]Reason:[/dim] {result.reason}")
        console.print(f"  [dim]Agent:[/dim] {result.agent_ref}")
        if result.policy_hash:
            console.print(f"  [dim]Policy:[/dim] {result.policy_hash[:20]}...")


@acc.command("batch")
@click.argument("agent_id")
@click.argument("tools", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def acc_batch(agent_id: str, tools: tuple, as_json: bool):
    """Check multiple tools at once.
    
    Example:
        substr8 acc batch analyst web_search memory_read shell_exec
    """
    from substr8.acc import check_batch
    
    results = check_batch(agent_id, list(tools))
    
    if as_json:
        import json
        console.print(json.dumps({k: v.to_dict() for k, v in results.items()}, indent=2))
    else:
        for tool_name, result in results.items():
            if result.allowed:
                console.print(f"[green]✓[/green] {tool_name}")
            else:
                console.print(f"[red]✗[/red] {tool_name} [dim]({result.reason})[/dim]")


@acc.command("policy")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def acc_policy(agent_id: str, as_json: bool):
    """Show the ACC policy for an agent.
    
    Example:
        substr8 acc policy analyst
    """
    from substr8.acc import load_policy_from_config
    
    policy = load_policy_from_config(agent_id)
    
    if policy is None:
        console.print(f"[yellow]No ACC policy found for agent '{agent_id}'[/yellow]")
        return
    
    if as_json:
        console.print(policy.to_json())
    else:
        console.print(f"[bold]Policy for {policy.agent_ref} v{policy.version}[/bold]")
        console.print(f"[dim]Hash:[/dim] {policy.policy_hash}")
        console.print()
        console.print("[bold]Rules:[/bold]")
        for i, rule in enumerate(policy.rules):
            action = "[green]allow[/green]" if rule.action.value == "allow" else "[red]deny[/red]"
            console.print(f"  {i+1}. {action} [cyan]{rule.tool}[/cyan]")


# === DCT Integration (Audit Ledger) ===

from substr8.dct.cli import main as dct_main

# Register DCT as a subcommand group
main.add_command(dct_main, name="dct")


# === Gateway (Docker Swarm Orchestration) ===

from substr8.gateway.cli import main as gateway_main

# Register Gateway as a subcommand group
main.add_command(gateway_main, name="gateway")


# === RIL (Runtime Integrity Layer) ===

from substr8.ril.cli import main as ril_main

# Register RIL as a subcommand group
main.add_command(ril_main, name="ril")


# === MCP Server ===

from substr8.mcp.cli import mcp as mcp_main

# Register MCP as a subcommand group
main.add_command(mcp_main, name="mcp")


# === Developer Tools ===

from substr8.dev.cli import dev as dev_main

# Register dev as a subcommand group
main.add_command(dev_main, name="dev")


# === Init Command (Top-level) ===

@main.command()
@click.argument("path", default=".")
@click.option("--framework", type=click.Choice(["all", "langgraph", "pydantic-ai", "autogen"]), 
              default="all", help="Framework to scaffold")
@click.option("--api-key", help="Pre-fill API key in .env")
@click.option("--minimal", is_flag=True, help="Create minimal structure (just one framework)")
def init(path: str, framework: str, api_key: str, minimal: bool):
    """Scaffold a new Substr8 project.
    
    Creates example agents for popular frameworks that connect to Substr8's
    governance plane via MCP.
    
    Examples:
    
        # Scaffold in current directory
        substr8 init
        
        # Scaffold in a new directory
        substr8 init my-project
        
        # Only LangGraph example
        substr8 init --framework langgraph
    """
    from substr8.init import scaffold_project
    
    result = scaffold_project(
        path=path,
        framework=framework,
        api_key=api_key,
        minimal=minimal
    )
    
    if result["success"]:
        console.print(f"[green]✓[/green] Created project in [bold]{result['path']}[/bold]")
        console.print()
        for f in result["files"][:10]:
            console.print(f"  {f}")
        if len(result["files"]) > 10:
            console.print(f"  ... and {len(result['files']) - 10} more files")
        console.print()
        console.print("[bold]Next steps:[/bold]")
        console.print(f"  1. cd {result['path']}")
        console.print("  2. substr8 mcp start --local")
        console.print("  3. python examples/langgraph/agent.py")
    else:
        console.print(f"[red]✗[/red] {result['error']}")


# === RunProof Commands (Top-level) ===

from substr8.runproof.cli import run as runproof_run, verify as runproof_verify, export as runproof_export, badge as runproof_badge

# Register top-level commands
main.add_command(runproof_run, name="run")
main.add_command(runproof_verify, name="verify")
main.add_command(runproof_export, name="export")
main.add_command(runproof_badge, name="badge")


# === Info Commands ===

@main.command()
def info():
    """Show Substr8 platform information."""
    import platform
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    
    # Header
    console.print()
    console.print("[bold cyan]  ███████╗██╗   ██╗██████╗ ███████╗████████╗██████╗  █████╗ [/bold cyan]")
    console.print("[bold cyan]  ██╔════╝██║   ██║██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔══██╗[/bold cyan]")
    console.print("[bold cyan]  ███████╗██║   ██║██████╔╝███████╗   ██║   ██████╔╝╚█████╔╝[/bold cyan]")
    console.print("[bold cyan]  ╚════██║██║   ██║██╔══██╗╚════██║   ██║   ██╔══██╗██╔══██╗[/bold cyan]")
    console.print("[bold cyan]  ███████║╚██████╔╝██████╔╝███████║   ██║   ██║  ██║╚█████╔╝[/bold cyan]")
    console.print("[bold cyan]  ╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚════╝ [/bold cyan]")
    console.print()
    console.print("[dim]  Verifiable AI Infrastructure[/dim]")
    console.print()
    
    # Components table
    table = Table(show_header=True, box=box.ROUNDED, title="[bold]Components[/bold]")
    table.add_column("Module", style="cyan", width=8)
    table.add_column("Status", width=10)
    table.add_column("Description", style="dim")
    
    table.add_row("fdaa", "[green]✓ ready[/green]", "File-Driven Agent Architecture")
    table.add_row("gam", "[green]✓ ready[/green]", "Git-Native Agent Memory")
    table.add_row("acc", "[green]✓ ready[/green]", "Agent Capability Control")
    table.add_row("dct", "[green]✓ ready[/green]", "Deterministic Capability Tokens")
    table.add_row("ril", "[green]✓ ready[/green]", "Runtime Integrity Layer")
    table.add_row("mcp", "[green]✓ ready[/green]", "MCP Server - Universal framework bridge")
    table.add_row("gateway", "[green]✓ ready[/green]", "Docker Swarm orchestration")
    
    console.print(table)
    console.print()
    
    # Quick start
    console.print("[bold]Quick Start[/bold]")
    console.print("  [cyan]substr8 fdaa init my-agent[/cyan]    Create an agent workspace")
    console.print("  [cyan]substr8 gam init[/cyan]              Initialize memory in current repo")
    console.print("  [cyan]substr8 mcp start[/cyan]             Start MCP server for any framework")
    console.print("  [cyan]substr8 dev init[/cyan]              Scaffold LangGraph/AutoGen examples")
    console.print()
    
    # Links
    console.print("[bold]Links[/bold]")
    console.print("  [dim]Docs:[/dim]      https://substr8labs.com/docs")
    console.print("  [dim]GitHub:[/dim]    https://github.com/Substr8-Labs")
    console.print("  [dim]PyPI:[/dim]      https://pypi.org/project/substr8")
    console.print()
    
    # Version & system
    console.print(f"[dim]v0.9.0 • Python {platform.python_version()} • {platform.system()} {platform.machine()}[/dim]")
    console.print()


# === Cloud Connect ===

@main.command()
@click.option("--api-key", envvar="SUBSTR8_API_KEY", help="API key for cloud access")
@click.option("--org", help="Organization ID")
def connect(api_key: str, org: str):
    """Connect this project to Substr8 Cloud.
    
    Unlocks:
    - Hosted MCP server (mcp.substr8labs.com)
    - RunProof registry & verification
    - Team dashboards
    - Higher rate limits
    
    \b
    Examples:
        substr8 connect --api-key sk-xxx
        substr8 connect  # Opens browser to get key
    """
    import os
    import webbrowser
    from pathlib import Path
    from rich.prompt import Prompt
    
    console.print()
    console.print("[bold cyan]Substr8 Cloud Connect[/bold cyan]")
    console.print()
    
    if not api_key:
        console.print("No API key provided. Opening browser to get one...")
        console.print()
        webbrowser.open("https://substr8labs.com/settings/api-keys")
        api_key = Prompt.ask("Paste your API key")
    
    if not api_key or len(api_key) < 10:
        console.print("[red]✗ Invalid API key[/red]")
        raise SystemExit(1)
    
    # Verify key with cloud
    import urllib.request
    import json
    
    try:
        req = urllib.request.Request(
            "https://mcp.substr8labs.com/auth/verify",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            org_name = data.get("org", "Personal")
    except Exception as e:
        # For now, accept any key format (cloud not fully live)
        org_name = org or "Personal"
        console.print(f"[yellow]⚠ Could not verify key (cloud API pending), proceeding...[/yellow]")
    
    # Save to config
    config_dir = Path.home() / ".config" / "substr8"
    config_dir.mkdir(parents=True, exist_ok=True)
    
    config_file = config_dir / "config.json"
    config = {}
    if config_file.exists():
        config = json.loads(config_file.read_text())
    
    config["api_key"] = api_key
    config["org"] = org_name
    config["cloud_enabled"] = True
    
    config_file.write_text(json.dumps(config, indent=2))
    
    # Also save to .env if in a project
    env_file = Path(".env")
    if env_file.exists():
        env_content = env_file.read_text()
        if "SUBSTR8_API_KEY" not in env_content:
            with open(env_file, "a") as f:
                f.write(f"\nSUBSTR8_API_KEY={api_key}\n")
            console.print("[dim]Added SUBSTR8_API_KEY to .env[/dim]")
    
    console.print()
    console.print("[green]✓ Connected to Substr8 Cloud[/green]")
    console.print()
    console.print(f"  [dim]Organization:[/dim]  {org_name}")
    console.print(f"  [dim]MCP Endpoint:[/dim]  https://mcp.substr8labs.com")
    console.print(f"  [dim]Config:[/dim]        {config_file}")
    console.print()
    console.print("[bold]You're all set![/bold]")
    console.print("  Run [cyan]substr8 run agent.py[/cyan] to execute with cloud governance.")
    console.print()


@main.command()
def disconnect():
    """Disconnect from Substr8 Cloud."""
    import json
    from pathlib import Path
    
    config_file = Path.home() / ".config" / "substr8" / "config.json"
    
    if config_file.exists():
        config = json.loads(config_file.read_text())
        config["cloud_enabled"] = False
        config.pop("api_key", None)
        config_file.write_text(json.dumps(config, indent=2))
        console.print("[green]✓ Disconnected from Substr8 Cloud[/green]")
    else:
        console.print("[yellow]Not connected to cloud[/yellow]")


@main.command()
def status():
    """Show connection and governance status."""
    import json
    from pathlib import Path
    
    console.print()
    console.print("[bold]Substr8 Status[/bold]")
    console.print()
    
    # Check cloud connection
    config_file = Path.home() / ".config" / "substr8" / "config.json"
    if config_file.exists():
        config = json.loads(config_file.read_text())
        if config.get("cloud_enabled"):
            console.print(f"  [green]●[/green] Cloud: Connected ({config.get('org', 'Personal')})")
        else:
            console.print("  [yellow]○[/yellow] Cloud: Disconnected")
    else:
        console.print("  [dim]○[/dim] Cloud: Not configured")
    
    # Check local MCP
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:9988/health", timeout=2) as resp:
            console.print("  [green]●[/green] Local MCP: Running (localhost:9988)")
    except:
        console.print("  [dim]○[/dim] Local MCP: Not running")
    
    # Check project
    if Path("agents").exists() or Path("agent.md").exists():
        console.print("  [green]●[/green] Project: Agent workspace detected")
    elif Path(".git").exists():
        console.print("  [yellow]○[/yellow] Project: Git repo (not initialized)")
    else:
        console.print("  [dim]○[/dim] Project: Not in a project")
    
    console.print()


if __name__ == "__main__":
    main()


# === Registry Integration ===

from substr8.registry.cli import main as registry_main

# Register Registry as a subcommand group
main.add_command(registry_main, name="registry")


# === Proof Commands (v2 format) ===

@main.group()
def proof():
    """RunProof v2 verification commands."""
    pass


@proof.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def verify_v2(file: str, as_json: bool):
    """Verify a RunProof v2 JSON file.
    
    Example:
        substr8 proof verify runproof.json
    """
    import json as json_module
    from pathlib import Path
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    
    from substr8.runproof.v2 import RunProof, verify_runproof
    
    path = Path(file)
    
    try:
        with open(path) as f:
            data = json_module.load(f)
        
        # Verify
        result = verify_runproof(data)
        
        if as_json:
            console.print(json_module.dumps(result.summary, indent=2))
            return
        
        if result.valid:
            console.print()
            console.print(Panel(
                "[bold green]✓ RunProof: VALID[/bold green]",
                title="Verification Result",
                border_style="green",
                box=box.ROUNDED
            ))
            console.print()
            
            # Summary table
            table = Table(show_header=False, box=box.SIMPLE)
            table.add_column("Field", style="dim")
            table.add_column("Value")
            
            table.add_row("Proof ID", result.proof_id or "-")
            table.add_row("Run ID", result.run_id or "-")
            table.add_row("Agent", result.agent_id or "-")
            
            console.print(table)
            console.print()
            
            # Checks
            console.print("[bold]Checks:[/bold]")
            for check in result.checks:
                icon = "[green]✓[/green]" if check.passed else "[red]✗[/red]"
                console.print(f"  {icon} {check.name}")
            console.print()
            
        else:
            console.print()
            console.print(Panel(
                f"[bold red]✗ RunProof: INVALID[/bold red]\n\n" + "\n".join(result.errors),
                title="Verification Failed",
                border_style="red",
                box=box.ROUNDED
            ))
            console.print()
            import sys
            sys.exit(1)
            
    except json_module.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] Invalid JSON: {e}")
        import sys
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        import sys
        sys.exit(1)


# Alias the verify command under proof group
proof.add_command(verify_v2, name="verify")


@proof.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--events", "-e", is_flag=True, help="Show event timeline")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def inspect(file: str, events: bool, as_json: bool):
    """Inspect RunProof v2 details.
    
    Example:
        substr8 proof inspect runproof.json
        substr8 proof inspect runproof.json --events
    """
    import json as json_module
    from pathlib import Path
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    
    from substr8.runproof.v2 import RunProof, verify_runproof
    
    path = Path(file)
    
    try:
        with open(path) as f:
            data = json_module.load(f)
        
        proof_obj = RunProof.model_validate(data)
        result = verify_runproof(data)
        
        if as_json:
            output = {
                "valid": result.valid,
                "proof_id": proof_obj.header.proof_id,
                "run_id": proof_obj.header.run_id,
                "agent_id": proof_obj.header.agent_id,
                "runtime": proof_obj.header.runtime,
                "status": proof_obj.header.status.value,
                "event_count": len(proof_obj.trace),
                "started_at": proof_obj.header.started_at.isoformat() if proof_obj.header.started_at else None,
                "ended_at": proof_obj.header.ended_at.isoformat() if proof_obj.header.ended_at else None,
            }
            console.print_json(json_module.dumps(output))
            return
        
        # Header
        status_color = "green" if result.valid else "red"
        status_icon = "✓" if result.valid else "✗"
        
        console.print()
        console.print(Panel(
            f"[bold {status_color}]{status_icon} RunProof[/bold {status_color}]",
            title=proof_obj.header.proof_id,
            border_style=status_color,
            box=box.ROUNDED
        ))
        console.print()
        
        # Details table
        table = Table(title="Proof Details", box=box.ROUNDED)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        
        table.add_row("Run ID", proof_obj.header.run_id)
        table.add_row("Agent", proof_obj.header.agent_id)
        table.add_row("Runtime", f"{proof_obj.header.runtime} {proof_obj.header.runtime_version or ''}")
        table.add_row("Status", proof_obj.header.status.value)
        table.add_row("Events", str(len(proof_obj.trace)))
        
        if proof_obj.header.started_at:
            table.add_row("Started", proof_obj.header.started_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
        if proof_obj.header.ended_at:
            table.add_row("Ended", proof_obj.header.ended_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
        
        console.print(table)
        console.print()
        
        # Cryptographic details
        crypto_table = Table(title="Cryptographic Commitments", box=box.ROUNDED)
        crypto_table.add_column("Field", style="cyan")
        crypto_table.add_column("Value", style="dim")
        
        crypto_table.add_row("Event Root", proof_obj.commitments.event_root[:50] + "...")
        crypto_table.add_row("Proof Hash", proof_obj.commitments.proof_hash[:50] + "...")
        crypto_table.add_row("Algorithm", proof_obj.commitments.signature.algorithm.value)
        crypto_table.add_row("Public Key", proof_obj.identity.signer.public_key[:40] + "...")
        
        console.print(crypto_table)
        console.print()
        
        # Events timeline (if requested)
        if events:
            events_table = Table(title="Event Timeline", box=box.ROUNDED)
            events_table.add_column("#", style="dim", width=4)
            events_table.add_column("Type", style="cyan")
            events_table.add_column("Time", style="dim")
            events_table.add_column("Hash", style="dim", width=20)
            
            for entry in proof_obj.trace:
                time_str = entry.timestamp.strftime("%H:%M:%S.%f")[:-3] if entry.timestamp else "-"
                hash_short = entry.entry_hash.split(":")[-1][:16] + "..." if entry.entry_hash else "-"
                events_table.add_row(
                    str(entry.seq),
                    entry.type.value,
                    time_str,
                    hash_short
                )
            
            console.print(events_table)
            console.print()
            
    except json_module.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] Invalid JSON: {e}")
        import sys
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        import sys
        sys.exit(1)
