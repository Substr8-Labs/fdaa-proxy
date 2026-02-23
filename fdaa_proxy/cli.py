"""
FDAA Proxy CLI

Command-line interface for the FDAA Proxy gateway.
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from . import __version__
from .config import load_config, create_default_config, ProxyConfig


console = Console()


@click.group()
@click.version_option(__version__, prog_name="fdaa-proxy")
@click.option("--config", "-c", "config_path", type=click.Path(), help="Path to config file")
@click.pass_context
def cli(ctx, config_path: str):
    """FDAA Proxy - Governed MCP Gateway with Cryptographic Audit Trails"""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


# =============================================================================
# Server Commands
# =============================================================================

@cli.command()
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8766, type=int, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
@click.pass_context
def start(ctx, host: str, port: int, reload: bool):
    """Start the FDAA Proxy server."""
    config_path = ctx.obj.get("config_path")
    
    config = None
    if config_path and Path(config_path).exists():
        config = load_config(config_path)
        console.print(f"[green]✓[/green] Loaded config from {config_path}")
    
    # Override with CLI options
    if config:
        config.server.host = host
        config.server.port = port
        config.server.reload = reload
    
    console.print(Panel(
        f"[bold]FDAA Proxy v{__version__}[/bold]\n"
        f"Starting server on [cyan]http://{host}:{port}[/cyan]",
        title="🚀 Starting"
    ))
    
    from .server import main as server_main
    server_main(config_path)


@cli.command()
@click.pass_context
def status(ctx):
    """Show server status."""
    import httpx
    
    config_path = ctx.obj.get("config_path")
    port = 8766
    
    if config_path and Path(config_path).exists():
        config = load_config(config_path)
        port = config.server.port
    
    try:
        response = httpx.get(f"http://localhost:{port}/")
        data = response.json()
        
        console.print(Panel(
            f"[bold green]Running[/bold green]\n\n"
            f"Version: {data.get('version', 'unknown')}\n"
            f"Gateways: {data.get('gateways', 0)}\n"
            f"ACC: {'✓' if data.get('acc_enabled') else '✗'}\n"
            f"DCT: {'✓' if data.get('dct_enabled') else '✗'}",
            title="📊 FDAA Proxy Status"
        ))
    except Exception as e:
        console.print(f"[red]✗[/red] Server not running: {e}")
        sys.exit(1)


@cli.command()
def init():
    """Initialize a new configuration file."""
    config_path = Path("gateway.yaml")
    
    if config_path.exists():
        if not click.confirm(f"{config_path} already exists. Overwrite?"):
            return
    
    config_path.write_text(create_default_config())
    console.print(f"[green]✓[/green] Created {config_path}")
    console.print("\nEdit the file to configure your gateways, then run:")
    console.print("  [cyan]fdaa-proxy start -c gateway.yaml[/cyan]")


# =============================================================================
# Gateway Commands
# =============================================================================

@cli.group()
def gateways():
    """Manage MCP gateways."""
    pass


@gateways.command("list")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def gateways_list(port: int):
    """List connected gateways."""
    import httpx
    
    try:
        response = httpx.get(f"http://localhost:{port}/gateways")
        data = response.json()
        
        if not data.get("gateways"):
            console.print("[yellow]No gateways connected[/yellow]")
            return
        
        table = Table(title="Connected Gateways")
        table.add_column("ID", style="cyan")
        table.add_column("Connected", style="green")
        table.add_column("Tools", justify="right")
        table.add_column("Requests", justify="right")
        table.add_column("Pending", justify="right")
        
        for gw in data["gateways"]:
            stats = gw.get("stats", {})
            table.add_row(
                gw["gateway_id"],
                "✓" if gw["connected"] else "✗",
                str(stats.get("allowed_tools", 0)),
                str(stats.get("total_requests", 0)),
                str(stats.get("pending_approvals", 0)),
            )
        
        console.print(table)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to list gateways: {e}")
        sys.exit(1)


@gateways.command("connect")
@click.argument("gateway_id")
@click.argument("server")
@click.option("--env", "-e", multiple=True, help="Environment variable (KEY=VALUE)")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def gateways_connect(gateway_id: str, server: str, env: tuple, port: int):
    """Connect a new MCP gateway."""
    import httpx
    
    # Parse env vars
    env_dict = {}
    for e in env:
        if "=" in e:
            k, v = e.split("=", 1)
            env_dict[k] = v
    
    try:
        response = httpx.post(
            f"http://localhost:{port}/gateways",
            json={
                "gateway_id": gateway_id,
                "server": server,
                "env": env_dict,
                "policy": {"mode": "allowlist"}
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            console.print(f"[green]✓[/green] Connected gateway: {gateway_id}")
            console.print(f"  Server: {server}")
            console.print(f"  Tools: {data.get('server_info', {}).get('allowed_tools', 0)}")
        else:
            console.print(f"[red]✗[/red] Failed: {response.json()}")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to connect: {e}")
        sys.exit(1)


@gateways.command("disconnect")
@click.argument("gateway_id")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def gateways_disconnect(gateway_id: str, port: int):
    """Disconnect a gateway."""
    import httpx
    
    try:
        response = httpx.delete(f"http://localhost:{port}/gateways/{gateway_id}")
        
        if response.status_code == 200:
            console.print(f"[green]✓[/green] Disconnected: {gateway_id}")
        else:
            console.print(f"[red]✗[/red] Failed: {response.json()}")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to disconnect: {e}")
        sys.exit(1)


@gateways.command("tools")
@click.argument("gateway_id")
@click.option("--all", "show_all", is_flag=True, help="Show all tools (including blocked)")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def gateways_tools(gateway_id: str, show_all: bool, port: int):
    """List tools from a gateway."""
    import httpx
    
    try:
        response = httpx.get(
            f"http://localhost:{port}/gateways/{gateway_id}/tools",
            params={"all": show_all}
        )
        
        if response.status_code != 200:
            console.print(f"[red]✗[/red] Gateway not found: {gateway_id}")
            sys.exit(1)
        
        data = response.json()
        
        table = Table(title=f"Tools - {gateway_id}")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        
        for tool in data.get("tools", []):
            table.add_row(
                tool["name"],
                tool.get("description", "")[:60] + "..." if len(tool.get("description", "")) > 60 else tool.get("description", "")
            )
        
        console.print(table)
        console.print(f"\nTotal: {data.get('count', 0)} tools")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


# =============================================================================
# Audit Commands
# =============================================================================

@cli.group()
def audit():
    """Query and verify audit logs."""
    pass


@audit.command("list")
@click.option("--gateway", "-g", help="Filter by gateway ID")
@click.option("--tool", "-t", help="Filter by tool name")
@click.option("--limit", "-n", default=20, type=int, help="Max entries")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def audit_list(gateway: str, tool: str, limit: int, port: int):
    """List audit entries."""
    import httpx
    
    try:
        params = {"limit": limit}
        if gateway:
            params["gateway_id"] = gateway
        if tool:
            params["tool"] = tool
        
        response = httpx.get(f"http://localhost:{port}/audit", params=params)
        data = response.json()
        
        table = Table(title="Audit Log")
        table.add_column("Timestamp", style="dim")
        table.add_column("Gateway")
        table.add_column("Tool", style="cyan")
        table.add_column("Persona")
        table.add_column("Status")
        
        for entry in data.get("entries", []):
            status = "[green]✓[/green]" if not entry.get("error") else f"[red]✗[/red]"
            table.add_row(
                entry["timestamp"][:19],
                entry["gateway_id"],
                entry.get("tool") or "-",
                entry.get("persona") or "-",
                status
            )
        
        console.print(table)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@audit.command("verify")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def audit_verify(port: int):
    """Verify audit chain integrity."""
    import httpx
    
    try:
        response = httpx.get(f"http://localhost:{port}/audit/verify")
        data = response.json()
        
        if data.get("valid"):
            console.print(Panel(
                f"[bold green]Chain Valid[/bold green]\n\n"
                f"Entries verified: {data.get('entries_checked', 0)}",
                title="✓ Integrity Check"
            ))
        else:
            console.print(Panel(
                f"[bold red]Chain Invalid[/bold red]\n\n"
                f"First invalid: {data.get('first_invalid', 'unknown')}\n"
                f"Error: {data.get('error', 'unknown')}",
                title="✗ Integrity Check"
            ))
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@audit.command("export")
@click.option("--output", "-o", type=click.Path(), help="Output file")
@click.option("--format", "-f", "fmt", default="json", type=click.Choice(["json", "jsonl"]))
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def audit_export(output: str, fmt: str, port: int):
    """Export audit log."""
    import httpx
    
    try:
        response = httpx.get(f"http://localhost:{port}/audit", params={"limit": 100000})
        data = response.json()
        
        entries = data.get("entries", [])
        
        if fmt == "json":
            content = json.dumps(entries, indent=2)
        else:
            content = "\n".join(json.dumps(e) for e in entries)
        
        if output:
            Path(output).write_text(content)
            console.print(f"[green]✓[/green] Exported {len(entries)} entries to {output}")
        else:
            console.print(content)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


# =============================================================================
# Approval Commands
# =============================================================================

@cli.group()
def approvals():
    """Manage pending approvals."""
    pass


@approvals.command("list")
@click.option("--gateway", "-g", help="Filter by gateway ID")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def approvals_list(gateway: str, port: int):
    """List pending approvals."""
    import httpx
    
    try:
        # Get all gateways first
        response = httpx.get(f"http://localhost:{port}/gateways")
        gateways_data = response.json()
        
        all_pending = []
        for gw in gateways_data.get("gateways", []):
            if gateway and gw["gateway_id"] != gateway:
                continue
            
            response = httpx.get(f"http://localhost:{port}/gateways/{gw['gateway_id']}/pending")
            pending = response.json().get("pending", [])
            for p in pending:
                p["gateway_id"] = gw["gateway_id"]
                all_pending.append(p)
        
        if not all_pending:
            console.print("[green]No pending approvals[/green]")
            return
        
        table = Table(title="Pending Approvals")
        table.add_column("ID", style="cyan")
        table.add_column("Gateway")
        table.add_column("Tool")
        table.add_column("Created")
        
        for p in all_pending:
            table.add_row(
                p["id"],
                p["gateway_id"],
                p["tool"],
                p["created_at"][:19]
            )
        
        console.print(table)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@approvals.command("approve")
@click.argument("gateway_id")
@click.argument("request_id")
@click.option("--by", "-b", default="cli", help="Approver identity")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def approvals_approve(gateway_id: str, request_id: str, by: str, port: int):
    """Approve a pending request."""
    import httpx
    
    try:
        response = httpx.post(
            f"http://localhost:{port}/gateways/{gateway_id}/approve/{request_id}",
            json={"approved": True, "approved_by": by}
        )
        
        if response.status_code == 200:
            console.print(f"[green]✓[/green] Approved: {request_id}")
        else:
            console.print(f"[red]✗[/red] Failed: {response.json()}")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@approvals.command("deny")
@click.argument("gateway_id")
@click.argument("request_id")
@click.option("--by", "-b", default="cli", help="Denier identity")
@click.option("--port", "-p", default=8766, type=int, help="Server port")
def approvals_deny(gateway_id: str, request_id: str, by: str, port: int):
    """Deny a pending request."""
    import httpx
    
    try:
        response = httpx.post(
            f"http://localhost:{port}/gateways/{gateway_id}/approve/{request_id}",
            json={"approved": False, "approved_by": by}
        )
        
        if response.status_code == 200:
            console.print(f"[green]✓[/green] Denied: {request_id}")
        else:
            console.print(f"[red]✗[/red] Failed: {response.json()}")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


# =============================================================================
# ACC (Agent Capability Certificate) Commands
# =============================================================================

@cli.group()
def acc():
    """ACC key and token management."""
    pass


@acc.command("keygen")
@click.option("--output", "-o", default="./acc-keys", help="Output directory for keys")
@click.option("--key-id", help="Custom key ID (auto-generated if not provided)")
def acc_keygen(output: str, key_id: str):
    """Generate a new ACC ED25519 key pair."""
    try:
        from .acc import generate_keypair, HAS_CRYPTO
        
        if not HAS_CRYPTO:
            console.print("[red]✗[/red] cryptography library required for key generation")
            console.print("Install with: pip install cryptography")
            sys.exit(1)
        
        key_pair = generate_keypair(Path(output))
        
        console.print(Panel(
            f"[bold green]Key pair generated![/bold green]\n\n"
            f"Key ID: [cyan]{key_pair.key_id}[/cyan]\n"
            f"DID: [dim]{key_pair.did}[/dim]\n"
            f"Public Key (b64): [dim]{key_pair.public_key_b64}[/dim]\n\n"
            f"Saved to: [cyan]{output}/[/cyan]",
            title="🔐 ACC Key Generation"
        ))
        
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@acc.command("sign")
@click.option("--key-path", "-k", required=True, help="Path to key directory")
@click.option("--subject", "-s", required=True, help="Token subject (e.g., agent:ada)")
@click.option("--capabilities", "-c", multiple=True, required=True, help="Capabilities to grant")
@click.option("--expires-in", "-e", default=3600, type=int, help="Expiration in seconds")
@click.option("--issuer", "-i", default="https://acc.substr8labs.com", help="Issuer URL")
def acc_sign(key_path: str, subject: str, capabilities: tuple, expires_in: int, issuer: str):
    """Create a signed ACC token."""
    try:
        from .acc import ACCKeyPair, ACCSigner, HAS_CRYPTO
        
        if not HAS_CRYPTO:
            console.print("[red]✗[/red] cryptography library required")
            sys.exit(1)
        
        key_pair = ACCKeyPair.load(Path(key_path))
        signer = ACCSigner(key_pair)
        
        token = signer.create_token(
            subject=subject,
            capabilities=list(capabilities),
            issuer=issuer,
            expires_in_seconds=expires_in,
        )
        
        console.print(Panel(
            f"[bold green]Token created![/bold green]\n\n"
            f"Subject: [cyan]{subject}[/cyan]\n"
            f"Capabilities: [cyan]{', '.join(capabilities)}[/cyan]\n"
            f"Expires in: {expires_in}s\n\n"
            f"[dim]Token:[/dim]\n{token}",
            title="🎫 ACC Token"
        ))
        
        # Also print just the token for piping
        console.print(f"\n[dim]Raw token:[/dim]")
        print(token)
        
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@acc.command("verify")
@click.argument("token")
@click.option("--key-path", "-k", help="Path to public key")
@click.option("--public-key", "-p", help="Base64-encoded public key")
def acc_verify(token: str, key_path: str, public_key: str):
    """Verify an ACC token signature."""
    try:
        from .acc import ACCVerifier, HAS_CRYPTO
        import base64
        
        if not HAS_CRYPTO:
            console.print("[red]✗[/red] cryptography library required")
            sys.exit(1)
        
        # Load public key
        pub_key_bytes = None
        if key_path:
            key_file = Path(key_path)
            if key_file.is_dir():
                key_file = key_file / "public.key"
            with open(key_file, "rb") as f:
                pub_key_bytes = f.read()
        elif public_key:
            pub_key_bytes = base64.urlsafe_b64decode(public_key + "==")
        else:
            console.print("[yellow]⚠[/yellow] No public key provided, will try to verify from embedded key ID")
        
        verifier = ACCVerifier(pub_key_bytes)
        valid, payload, error = verifier.verify(token)
        
        if valid:
            console.print(Panel(
                f"[bold green]✓ Token valid![/bold green]\n\n"
                f"Token ID: [cyan]{payload.get('token_id')}[/cyan]\n"
                f"Subject: [cyan]{payload.get('subject')}[/cyan]\n"
                f"Issuer: {payload.get('issuer')}\n"
                f"Capabilities: {', '.join(payload.get('capabilities', []))}\n"
                f"Expires: {payload.get('expires_at')}",
                title="🔐 ACC Verification"
            ))
        else:
            console.print(f"[red]✗[/red] Invalid: {error}")
            sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


# =============================================================================
# SafeMode Snapshot Commands
# =============================================================================

@cli.group()
def snapshot():
    """SafeMode snapshot management."""
    pass


@snapshot.command("create")
@click.argument("source_path")
@click.option("--retention", "-r", default=30, type=int, help="Retention period in days")
@click.option("--gateway-id", "-g", default="cli", help="Gateway ID")
@click.option("--agent-id", "-a", help="Agent ID")
@click.option("--storage", "-s", default="./snapshots", help="Snapshot storage path")
def snapshot_create(source_path: str, retention: int, gateway_id: str, agent_id: str, storage: str):
    """Create a SafeMode snapshot."""
    from .safemode import SnapshotManager, SnapshotConfig
    
    config = SnapshotConfig(storage_path=Path(storage))
    manager = SnapshotManager(config)
    
    try:
        snapshot = manager.create(
            source_path=Path(source_path),
            retention_days=retention,
            gateway_id=gateway_id,
            agent_id=agent_id,
        )
        
        console.print(Panel(
            f"[bold green]Snapshot created![/bold green]\n\n"
            f"ID: [cyan]{snapshot.id}[/cyan]\n"
            f"Hash: [dim]{snapshot.hash[:16]}...[/dim]\n"
            f"Size: {snapshot.size_bytes:,} bytes ({snapshot.file_count} files)\n"
            f"Locked until: [yellow]{snapshot.retention_until.strftime('%Y-%m-%d %H:%M')}[/yellow]\n"
            f"Signed: {'✓' if snapshot.signature else '✗'}",
            title="🔒 SafeMode Snapshot"
        ))
        
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@snapshot.command("list")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--include-expired", is_flag=True, help="Include expired snapshots")
@click.option("--storage", "-s", default="./snapshots", help="Snapshot storage path")
def snapshot_list(agent_id: str, include_expired: bool, storage: str):
    """List SafeMode snapshots."""
    from .safemode import SnapshotManager, SnapshotConfig
    
    config = SnapshotConfig(storage_path=Path(storage))
    manager = SnapshotManager(config)
    
    snapshots = manager.list(agent_id=agent_id, include_expired=include_expired)
    
    if not snapshots:
        console.print("[yellow]No snapshots found[/yellow]")
        return
    
    table = Table(title="SafeMode Snapshots")
    table.add_column("ID", style="cyan")
    table.add_column("Created")
    table.add_column("Locked Until")
    table.add_column("Size")
    table.add_column("Status")
    
    for s in snapshots:
        status = "[green]🔒 Locked[/green]" if s.is_locked() else "[dim]Unlocked[/dim]"
        table.add_row(
            s.id,
            s.created_at.strftime("%Y-%m-%d %H:%M"),
            s.retention_until.strftime("%Y-%m-%d %H:%M"),
            f"{s.size_bytes:,} B",
            status,
        )
    
    console.print(table)


@snapshot.command("verify")
@click.argument("snapshot_id")
@click.option("--storage", "-s", default="./snapshots", help="Snapshot storage path")
def snapshot_verify(snapshot_id: str, storage: str):
    """Verify snapshot integrity."""
    from .safemode import SnapshotManager, SnapshotConfig
    
    config = SnapshotConfig(storage_path=Path(storage))
    manager = SnapshotManager(config)
    
    result = manager.verify(snapshot_id)
    
    if result["valid"]:
        console.print(Panel(
            f"[bold green]✓ Snapshot valid![/bold green]\n\n"
            f"Snapshot: {snapshot_id}\n"
            f"Locked: {'Yes' if result['is_locked'] else 'No'}\n"
            f"Until: {result['retention_until']}\n"
            f"Checks: {result['checks']}",
            title="🔒 Verification"
        ))
    else:
        console.print(f"[red]✗[/red] Invalid: {result.get('error', 'Unknown error')}")
        sys.exit(1)


@snapshot.command("recover")
@click.argument("snapshot_id")
@click.argument("target_path")
@click.option("--storage", "-s", default="./snapshots", help="Snapshot storage path")
@click.option("--no-verify", is_flag=True, help="Skip hash verification")
def snapshot_recover(snapshot_id: str, target_path: str, storage: str, no_verify: bool):
    """Recover from a snapshot."""
    from .safemode import SnapshotManager, SnapshotConfig
    
    config = SnapshotConfig(storage_path=Path(storage))
    manager = SnapshotManager(config)
    
    try:
        success = manager.recover(
            snapshot_id=snapshot_id,
            target_path=Path(target_path),
            verify=not no_verify,
        )
        
        if success:
            console.print(f"[green]✓[/green] Recovered to {target_path}")
        else:
            console.print("[red]✗[/red] Recovery failed (hash mismatch)")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


@snapshot.command("delete")
@click.argument("snapshot_id")
@click.option("--force", is_flag=True, help="Force delete even if locked")
@click.option("--storage", "-s", default="./snapshots", help="Snapshot storage path")
def snapshot_delete(snapshot_id: str, force: bool, storage: str):
    """Delete a snapshot (if not locked)."""
    from .safemode import SnapshotManager, SnapshotConfig
    
    config = SnapshotConfig(storage_path=Path(storage))
    manager = SnapshotManager(config)
    
    try:
        success = manager.delete(snapshot_id, force=force)
        
        if success:
            console.print(f"[green]✓[/green] Deleted {snapshot_id}")
        else:
            console.print(f"[red]✗[/red] Cannot delete: snapshot is locked")
            console.print("Use --force to override (not recommended)")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        sys.exit(1)


# =============================================================================
# OpenClaw Proxy Commands
# =============================================================================

@cli.group()
def openclaw():
    """OpenClaw Gateway proxy commands."""
    pass


@openclaw.command("start")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8800, type=int, help="Port to bind to")
@click.option("--upstream", "-u", default="ws://localhost:18789", help="Upstream OpenClaw Gateway URL")
@click.option("--upstream-token", envvar="OPENCLAW_GATEWAY_TOKEN", help="Upstream gateway token")
@click.option("--require-acc", is_flag=True, help="Require ACC tokens for all connections")
@click.option("--acc-key", envvar="ACC_PUBLIC_KEY_PATH", help="Path to ACC public key for signature verification")
@click.option("--acc-issuer", envvar="ACC_ISSUER", help="Expected ACC token issuer")
@click.option("--audit-db", default="./openclaw-audit.db", help="Audit database path")
def openclaw_start(
    host: str, 
    port: int, 
    upstream: str, 
    upstream_token: str, 
    require_acc: bool, 
    acc_key: str,
    acc_issuer: str,
    audit_db: str
):
    """Start the OpenClaw Gateway proxy."""
    import asyncio
    from .dct import DCTLogger
    from .acc import ACCValidator
    from .openclaw.proxy import run_proxy
    
    # Determine ACC mode
    use_crypto = bool(acc_key)
    acc_mode = "ED25519" if use_crypto else "dev"
    
    console.print(Panel(
        f"[bold]FDAA OpenClaw Proxy[/bold]\n"
        f"Listening: [cyan]ws://{host}:{port}[/cyan]\n"
        f"Upstream: [cyan]{upstream}[/cyan]\n"
        f"ACC Required: {'Yes' if require_acc else 'No'}\n"
        f"ACC Mode: [{'green' if use_crypto else 'yellow'}]{acc_mode}[/{'green' if use_crypto else 'yellow'}]",
        title="🚀 Starting"
    ))
    
    # Initialize components
    dct_logger = DCTLogger(storage="sqlite", path=audit_db)
    
    # ACC validator - use crypto if key provided
    if use_crypto:
        acc_validator = ACCValidator(
            issuer=acc_issuer,
            public_key_path=acc_key,
            dev_mode=False,
        )
        console.print(f"[green]✓[/green] ACC crypto enabled with key: {acc_key}")
    else:
        acc_validator = ACCValidator(dev_mode=True)
        console.print(f"[yellow]⚠[/yellow] ACC in dev mode (structure validation only)")
    
    asyncio.run(run_proxy(
        host=host,
        port=port,
        upstream_url=upstream,
        upstream_token=upstream_token,
        acc_validator=acc_validator,
        dct_logger=dct_logger,
        require_acc=require_acc,
    ))


@openclaw.command("status")
@click.option("--port", "-p", default=8800, type=int, help="Proxy port")
def openclaw_status(port: int):
    """Check OpenClaw proxy status."""
    import httpx
    
    # The proxy is WebSocket-only for now, just check if port is listening
    import socket
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', port))
    sock.close()
    
    if result == 0:
        console.print(f"[green]✓[/green] OpenClaw proxy listening on port {port}")
    else:
        console.print(f"[red]✗[/red] OpenClaw proxy not running on port {port}")


# =============================================================================
# Verify Commands
# =============================================================================

@cli.group()
def verify():
    """Verification UI and audit chain tools."""
    pass


@verify.command("serve")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8080, type=int, help="Port to bind to")
@click.option("--jaeger-url", default="http://localhost:16686", help="Jaeger URL")
@click.option("--dct-path", default="/data/dct", help="DCT storage path")
def verify_serve(host: str, port: int, jaeger_url: str, dct_path: str):
    """Start the verification UI server."""
    import os
    os.environ["JAEGER_URL"] = jaeger_url
    os.environ["DCT_STORAGE_PATH"] = dct_path
    
    console.print(Panel.fit(
        f"[bold blue]FDAA Verification UI[/bold blue]\n\n"
        f"Server: http://{host}:{port}\n"
        f"Jaeger: {jaeger_url}\n"
        f"DCT Path: {dct_path}",
        title="🔐 Starting"
    ))
    
    from .verify import run_server
    run_server(host=host, port=port)


@verify.command("check")
@click.option("--dct-path", default="/data/dct", help="DCT storage path")
def verify_check(dct_path: str):
    """Verify DCT chain integrity."""
    import os
    os.environ["DCT_STORAGE_PATH"] = dct_path
    
    from .verify.app import load_dct_entries, verify_chain
    
    entries = load_dct_entries()
    result = verify_chain(entries)
    
    if result.valid:
        console.print(f"[green]✓[/green] Chain valid ({result.entries_checked} entries)")
    else:
        console.print(f"[red]✗[/red] Chain invalid!")
        for error in result.errors:
            console.print(f"  [red]•[/red] {error}")


# =============================================================================
# LLM Proxy Commands
# =============================================================================

@cli.group()
def llm():
    """LLM API proxy commands (Anthropic-compatible)."""
    pass


@llm.command("start")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8080, type=int, help="Port to bind to")
@click.option("--upstream", "-u", required=True, help="Upstream LLM API URL")
@click.option("--upstream-auth", help="Upstream auth token (Bearer)")
@click.option("--acc-enabled", is_flag=True, help="Require ACC tokens")
@click.option("--dct-path", default="/data/audit/dct.jsonl", help="DCT log path")
def llm_start(host: str, port: int, upstream: str, upstream_auth: str, acc_enabled: bool, dct_path: str):
    """Start LLM API proxy."""
    from .llm.proxy import LLMProxy
    
    console.print(Panel(
        f"[bold]FDAA LLM Proxy[/bold]\n"
        f"Upstream: [cyan]{upstream}[/cyan]\n"
        f"Listening: [cyan]http://{host}:{port}[/cyan]\n"
        f"ACC: {'✓ enabled' if acc_enabled else '✗ disabled'}\n"
        f"DCT: [cyan]{dct_path}[/cyan]",
        title="🚀 Starting"
    ))
    
    proxy = LLMProxy(
        upstream_url=upstream,
        upstream_auth=upstream_auth,
        acc_enabled=acc_enabled,
        dct_path=dct_path,
    )
    proxy.run(host=host, port=port)


# =============================================================================
# Main
# =============================================================================

def main():
    """Main entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
