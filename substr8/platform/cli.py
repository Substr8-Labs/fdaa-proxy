"""
Substr8 Platform CLI - Docker-based service orchestration

Manages the Substr8 governance services stack via Docker Compose or Swarm.
"""

import os
import subprocess
import shutil
import yaml
from pathlib import Path
from typing import Optional, List

import click
from rich.console import Console
from rich.table import Table

console = Console()

# Default config location
CONFIG_DIR = Path.home() / ".substr8"
CONFIG_FILE = CONFIG_DIR / "platform.yaml"
COMPOSE_FILE = CONFIG_DIR / "docker-compose.yml"

# Services in the platform
SERVICES = [
    "postgres",
    "gam",
    "acc",
    "runproof",
    "governed-flow",
    "governance-operator",
]

# Default ports
DEFAULT_PORTS = {
    "postgres": 5432,
    "gam": 8091,
    "acc": 8096,
    "runproof": 8097,
    "governed-flow": 8100,
    "governance-operator": 8101,
}


def get_compose_template() -> str:
    """Get the docker-compose template."""
    template_path = Path(__file__).parent / "templates" / "docker-compose.yml"
    if template_path.exists():
        return template_path.read_text()
    
    # Fallback: fetch from package
    import importlib.resources as pkg_resources
    return pkg_resources.read_text("substr8.platform.templates", "docker-compose.yml")


def ensure_config_dir():
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load platform configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config: dict):
    """Save platform configuration."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def check_docker() -> bool:
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def check_compose() -> bool:
    """Check if Docker Compose is available."""
    try:
        # Try docker compose (v2)
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10
        )
        if result.returncode == 0:
            return True
        
        # Try docker-compose (v1)
        result = subprocess.run(
            ["docker-compose", "version"],
            capture_output=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def run_compose(args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run docker compose command."""
    # Try docker compose v2 first
    try:
        cmd = ["docker", "compose"] + args
        result = subprocess.run(cmd, cwd=cwd or CONFIG_DIR)
        return result
    except FileNotFoundError:
        # Fall back to docker-compose v1
        cmd = ["docker-compose"] + args
        return subprocess.run(cmd, cwd=cwd or CONFIG_DIR)


@click.group()
def platform():
    """Substr8 Platform - Docker-based service orchestration.
    
    Manage the full Substr8 governance stack:
    - GAM (Memory)
    - ACC (Capability Control)
    - RunProof (Proof Builder)
    - Governed Flow (Execution Chain)
    - Governance Operator (Inspection UI)
    
    \b
    Examples:
        substr8 platform init
        substr8 platform start
        substr8 platform status
        substr8 platform logs runproof
    """
    pass


@platform.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config")
def platform_init(force: bool):
    """Initialize the platform configuration.
    
    Creates ~/.substr8/platform.yaml and docker-compose.yml
    """
    ensure_config_dir()
    
    # Check if already initialized
    if COMPOSE_FILE.exists() and not force:
        console.print("[yellow]Platform already initialized.[/yellow]")
        console.print(f"  Config: {CONFIG_FILE}")
        console.print(f"  Compose: {COMPOSE_FILE}")
        console.print("\nUse --force to reinitialize.")
        return
    
    # Write docker-compose.yml
    template = get_compose_template()
    COMPOSE_FILE.write_text(template)
    console.print(f"[green]✓[/green] Created {COMPOSE_FILE}")
    
    # Write default config
    config = {
        "mode": "compose",
        "registry": "ghcr.io/substr8-labs",
        "services": {name: {"enabled": True, "port": port} for name, port in DEFAULT_PORTS.items()},
    }
    save_config(config)
    console.print(f"[green]✓[/green] Created {CONFIG_FILE}")
    
    # Create .env file
    env_file = CONFIG_DIR / ".env"
    if not env_file.exists():
        env_content = """# Substr8 Platform Environment
POSTGRES_DB=gam
POSTGRES_USER=gam
POSTGRES_PASSWORD=substr8-dev

# Optional: Custom registry
# REGISTRY=ghcr.io/substr8-labs
"""
        env_file.write_text(env_content)
        console.print(f"[green]✓[/green] Created {env_file}")
    
    console.print("\n[bold]Platform initialized![/bold]")
    console.print("\nNext steps:")
    console.print("  1. Edit ~/.substr8/.env if needed")
    console.print("  2. Run: substr8 platform start")


@platform.command("start")
@click.option("--service", "-s", multiple=True, help="Start specific service(s)")
@click.option("--detach/--no-detach", "-d", default=True, help="Run in background")
@click.option("--build", is_flag=True, help="Build images before starting")
def platform_start(service: tuple, detach: bool, build: bool):
    """Start the platform services."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        console.print("Run: substr8 platform init")
        return
    
    if not check_docker():
        console.print("[red]Docker is not running.[/red]")
        return
    
    console.print("[bold]Starting Substr8 Platform...[/bold]\n")
    
    args = ["-f", str(COMPOSE_FILE), "up"]
    if detach:
        args.append("-d")
    if build:
        args.append("--build")
    if service:
        args.extend(service)
    
    run_compose(args)


@platform.command("stop")
@click.option("--service", "-s", multiple=True, help="Stop specific service(s)")
def platform_stop(service: tuple):
    """Stop the platform services."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    console.print("[bold]Stopping Substr8 Platform...[/bold]\n")
    
    args = ["-f", str(COMPOSE_FILE), "stop"]
    if service:
        args.extend(service)
    
    run_compose(args)


@platform.command("down")
@click.option("--volumes", "-v", is_flag=True, help="Remove volumes")
def platform_down(volumes: bool):
    """Stop and remove all platform containers."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    console.print("[bold]Stopping and removing Substr8 Platform...[/bold]\n")
    
    args = ["-f", str(COMPOSE_FILE), "down"]
    if volumes:
        args.append("-v")
    
    run_compose(args)


@platform.command("status")
def platform_status():
    """Show platform service status."""
    if not COMPOSE_FILE.exists():
        console.print("[yellow]Platform not initialized.[/yellow]")
        console.print("Run: substr8 platform init")
        return
    
    console.print("[bold]Substr8 Platform Status[/bold]\n")
    
    # Run docker compose ps
    run_compose(["-f", str(COMPOSE_FILE), "ps"])
    
    # Health check each service
    console.print("\n[bold]Service Health[/bold]\n")
    
    table = Table(show_header=True)
    table.add_column("Service")
    table.add_column("Port")
    table.add_column("Health")
    
    import httpx
    
    for service, port in DEFAULT_PORTS.items():
        if service == "postgres":
            # Skip postgres HTTP check
            table.add_row(service, str(port), "[dim]N/A[/dim]")
            continue
        
        try:
            response = httpx.get(f"http://localhost:{port}/health", timeout=2)
            if response.status_code == 200:
                table.add_row(service, str(port), "[green]✓ Healthy[/green]")
            else:
                table.add_row(service, str(port), f"[yellow]⚠ {response.status_code}[/yellow]")
        except Exception:
            table.add_row(service, str(port), "[red]✗ Unreachable[/red]")
    
    console.print(table)


@platform.command("logs")
@click.argument("service", required=False)
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.option("--tail", "-n", default=100, help="Number of lines to show")
def platform_logs(service: Optional[str], follow: bool, tail: int):
    """View platform service logs."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    args = ["-f", str(COMPOSE_FILE), "logs", "--tail", str(tail)]
    if follow:
        args.append("-f")
    if service:
        args.append(service)
    
    run_compose(args)


@platform.command("restart")
@click.argument("service", required=False)
def platform_restart(service: Optional[str]):
    """Restart platform services."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    console.print("[bold]Restarting services...[/bold]\n")
    
    args = ["-f", str(COMPOSE_FILE), "restart"]
    if service:
        args.append(service)
    
    run_compose(args)


@platform.command("build")
@click.argument("service", required=False)
@click.option("--no-cache", is_flag=True, help="Build without cache")
def platform_build(service: Optional[str], no_cache: bool):
    """Build platform service images."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    console.print("[bold]Building service images...[/bold]\n")
    
    args = ["-f", str(COMPOSE_FILE), "build"]
    if no_cache:
        args.append("--no-cache")
    if service:
        args.append(service)
    
    run_compose(args)


@platform.command("pull")
def platform_pull():
    """Pull latest platform images from registry."""
    if not COMPOSE_FILE.exists():
        console.print("[red]Platform not initialized.[/red]")
        return
    
    console.print("[bold]Pulling latest images...[/bold]\n")
    run_compose(["-f", str(COMPOSE_FILE), "pull"])


@platform.group("config")
def platform_config():
    """Platform configuration management."""
    pass


@platform_config.command("show")
def config_show():
    """Show current platform configuration."""
    config = load_config()
    if not config:
        console.print("[yellow]No configuration found.[/yellow]")
        console.print("Run: substr8 platform init")
        return
    
    console.print("[bold]Platform Configuration[/bold]\n")
    console.print(yaml.dump(config, default_flow_style=False))


@platform_config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value.
    
    Example: substr8 platform config set mode swarm
    """
    config = load_config()
    
    # Handle nested keys (e.g., services.gam.port)
    keys = key.split(".")
    current = config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]
    
    # Try to parse value as int/bool
    if value.isdigit():
        value = int(value)
    elif value.lower() in ("true", "false"):
        value = value.lower() == "true"
    
    current[keys[-1]] = value
    save_config(config)
    console.print(f"[green]✓[/green] Set {key} = {value}")


# Export for CLI registration
main = platform
