"""
Substr8 Gateway CLI - Docker Swarm orchestration

Commands:
    substr8 gateway status     Show stack health
    substr8 gateway start      Deploy/start a stack
    substr8 gateway stop       Remove a stack
    substr8 gateway logs       View service logs
    substr8 gateway upgrade    Pull images and redeploy
"""

import click
import subprocess
import json
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table

console = Console()

# Default stack configurations
STACKS = {
    "towerhq": {
        "file": "towerhq-stack.yml",
        "description": "TowerHQ production",
        "services": ["shared-gateway", "fdaa-proxy", "bridge", "verify", "jaeger"],
    },
    "towerhq-staging": {
        "file": "towerhq-staging-stack.yml", 
        "description": "TowerHQ staging",
        "services": ["shared-gateway", "fdaa-proxy", "bridge", "verify", "fdaa-api"],
    },
    "platform": {
        "file": "platform-stack.yml",
        "description": "Shared platform services",
        "services": ["gateway", "fdaa-proxy", "gam", "postgres", "bridge", "verify", "jaeger"],
    },
}

# Default stack directory
DEFAULT_STACK_DIR = Path("/home/node/.openclaw/workspace/fdaa-proxy/docker")


def run_docker(args: List[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a docker command."""
    cmd = ["docker"] + args
    return subprocess.run(cmd, capture_output=capture, text=True)


def get_stack_services(stack_name: str) -> List[dict]:
    """Get services for a stack."""
    result = run_docker(["stack", "services", stack_name, "--format", "json"])
    if result.returncode != 0:
        return []
    
    services = []
    for line in result.stdout.strip().split("\n"):
        if line:
            try:
                services.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return services


def get_all_services() -> List[dict]:
    """Get all services across all stacks."""
    result = run_docker(["service", "ls", "--format", "json"])
    if result.returncode != 0:
        return []
    
    services = []
    for line in result.stdout.strip().split("\n"):
        if line:
            try:
                services.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return services


@click.group()
def main():
    """Gateway management - Docker Swarm orchestration"""
    pass


@main.command("status")
@click.option("--stack", "-s", help="Filter by stack name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(stack: Optional[str], as_json: bool):
    """Show stack and service health.
    
    Examples:
        substr8 gateway status
        substr8 gateway status -s towerhq
        substr8 gateway status --json
    """
    # Check if Docker is available
    result = run_docker(["info", "--format", "{{.Swarm.LocalNodeState}}"])
    if result.returncode != 0:
        console.print("[red]Error: Docker not available[/red]")
        raise SystemExit(1)
    
    swarm_state = result.stdout.strip()
    if swarm_state != "active":
        console.print(f"[yellow]Warning: Swarm not active (state: {swarm_state})[/yellow]")
    
    # Get stack list
    result = run_docker(["stack", "ls", "--format", "json"])
    stacks = []
    for line in result.stdout.strip().split("\n"):
        if line:
            try:
                stacks.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    
    if stack:
        stacks = [s for s in stacks if s.get("Name") == stack]
    
    if as_json:
        output = {"swarm_state": swarm_state, "stacks": []}
        for s in stacks:
            services = get_stack_services(s["Name"])
            output["stacks"].append({
                "name": s["Name"],
                "services": len(services),
                "healthy": all(svc.get("Replicas", "0/0").split("/")[0] == svc.get("Replicas", "0/0").split("/")[1] for svc in services),
                "service_list": services,
            })
        click.echo(json.dumps(output, indent=2))
        return
    
    # Display table
    console.print(f"\n[bold]Swarm State:[/bold] {swarm_state}\n")
    
    if not stacks:
        console.print("[yellow]No stacks found[/yellow]")
        return
    
    for s in stacks:
        services = get_stack_services(s["Name"])
        healthy = sum(1 for svc in services if svc.get("Replicas", "0/0").split("/")[0] == svc.get("Replicas", "0/0").split("/")[1])
        
        console.print(f"[bold cyan]{s['Name']}[/bold cyan] ({healthy}/{len(services)} healthy)")
        
        table = Table(show_header=True, header_style="dim")
        table.add_column("Service", style="cyan")
        table.add_column("Replicas")
        table.add_column("Image")
        table.add_column("Ports")
        
        for svc in services:
            name = svc.get("Name", "").replace(f"{s['Name']}_", "")
            replicas = svc.get("Replicas", "0/0")
            image = svc.get("Image", "").split("@")[0]  # Remove digest
            ports = svc.get("Ports", "")
            
            # Color replicas based on health
            current, desired = replicas.split("/")
            if current == desired and int(current) > 0:
                replicas_styled = f"[green]{replicas}[/green]"
            elif int(current) > 0:
                replicas_styled = f"[yellow]{replicas}[/yellow]"
            else:
                replicas_styled = f"[red]{replicas}[/red]"
            
            table.add_row(name, replicas_styled, image[:40], ports[:30] if ports else "-")
        
        console.print(table)
        console.print()


@main.command("start")
@click.argument("stack_name")
@click.option("--file", "-f", type=click.Path(exists=True), help="Stack file path")
@click.option("--dir", "-d", "stack_dir", type=click.Path(exists=True), help="Stack directory")
def start(stack_name: str, file: Optional[str], stack_dir: Optional[str]):
    """Deploy/start a stack.
    
    Examples:
        substr8 gateway start towerhq
        substr8 gateway start towerhq -f /path/to/stack.yml
    """
    # Determine stack file
    if file:
        stack_file = Path(file)
    else:
        base_dir = Path(stack_dir) if stack_dir else DEFAULT_STACK_DIR
        
        if stack_name in STACKS:
            stack_file = base_dir / STACKS[stack_name]["file"]
        else:
            stack_file = base_dir / f"{stack_name}-stack.yml"
    
    if not stack_file.exists():
        console.print(f"[red]Stack file not found: {stack_file}[/red]")
        raise SystemExit(1)
    
    console.print(f"[cyan]Deploying stack:[/cyan] {stack_name}")
    console.print(f"[dim]File: {stack_file}[/dim]")
    
    result = run_docker(["stack", "deploy", "-c", str(stack_file), stack_name], capture=False)
    
    if result.returncode == 0:
        console.print(f"[green]✓ Stack {stack_name} deployed[/green]")
    else:
        console.print(f"[red]✗ Failed to deploy {stack_name}[/red]")
        raise SystemExit(1)


@main.command("stop")
@click.argument("stack_name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def stop(stack_name: str, force: bool):
    """Remove a stack.
    
    Examples:
        substr8 gateway stop towerhq-staging
        substr8 gateway stop towerhq -f
    """
    if not force:
        if not click.confirm(f"Remove stack {stack_name}?"):
            console.print("[yellow]Cancelled[/yellow]")
            return
    
    console.print(f"[cyan]Removing stack:[/cyan] {stack_name}")
    
    result = run_docker(["stack", "rm", stack_name], capture=False)
    
    if result.returncode == 0:
        console.print(f"[green]✓ Stack {stack_name} removed[/green]")
    else:
        console.print(f"[red]✗ Failed to remove {stack_name}[/red]")
        raise SystemExit(1)


@main.command("logs")
@click.argument("service")
@click.option("--stack", "-s", help="Stack name (prefix)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.option("--tail", "-n", default=100, help="Number of lines to show")
def logs(service: str, stack: Optional[str], follow: bool, tail: int):
    """View service logs.
    
    Examples:
        substr8 gateway logs fdaa-proxy -s towerhq
        substr8 gateway logs towerhq_fdaa-proxy -f
        substr8 gateway logs shared-gateway -s platform -n 50
    """
    # Build service name
    if stack and not service.startswith(f"{stack}_"):
        service_name = f"{stack}_{service}"
    else:
        service_name = service
    
    args = ["service", "logs", service_name, f"--tail={tail}"]
    if follow:
        args.append("--follow")
    
    console.print(f"[dim]Logs for {service_name}[/dim]\n")
    run_docker(args, capture=False)


@main.command("upgrade")
@click.argument("stack_name")
@click.option("--pull", "-p", is_flag=True, help="Pull latest images first")
def upgrade(stack_name: str, pull: bool):
    """Upgrade a stack (pull images and redeploy).
    
    Examples:
        substr8 gateway upgrade towerhq
        substr8 gateway upgrade towerhq --pull
    """
    base_dir = DEFAULT_STACK_DIR
    
    if stack_name in STACKS:
        stack_file = base_dir / STACKS[stack_name]["file"]
    else:
        stack_file = base_dir / f"{stack_name}-stack.yml"
    
    if not stack_file.exists():
        console.print(f"[red]Stack file not found: {stack_file}[/red]")
        raise SystemExit(1)
    
    if pull:
        console.print("[cyan]Pulling latest images...[/cyan]")
        # Parse stack file to get images (simplified - would need yaml parsing)
        result = run_docker(["compose", "-f", str(stack_file), "pull"], capture=False)
        if result.returncode != 0:
            console.print("[yellow]Warning: Some images may not have pulled[/yellow]")
    
    console.print(f"[cyan]Redeploying stack:[/cyan] {stack_name}")
    
    result = run_docker(["stack", "deploy", "-c", str(stack_file), stack_name], capture=False)
    
    if result.returncode == 0:
        console.print(f"[green]✓ Stack {stack_name} upgraded[/green]")
    else:
        console.print(f"[red]✗ Failed to upgrade {stack_name}[/red]")
        raise SystemExit(1)


@main.command("ps")
@click.option("--stack", "-s", help="Filter by stack")
def ps(stack: Optional[str]):
    """List running tasks (containers).
    
    Examples:
        substr8 gateway ps
        substr8 gateway ps -s towerhq
    """
    if stack:
        result = run_docker(["stack", "ps", stack, "--format", 
            "table {{.Name}}\t{{.Node}}\t{{.CurrentState}}\t{{.Error}}"], capture=False)
    else:
        result = run_docker(["service", "ps", "$(docker service ls -q)", "--format",
            "table {{.Name}}\t{{.Node}}\t{{.CurrentState}}\t{{.Error}}"], capture=False)


@main.command("health")
def health():
    """Quick health check of all stacks.
    
    Returns exit code 0 if all healthy, 1 otherwise.
    """
    services = get_all_services()
    
    total = len(services)
    healthy = 0
    unhealthy = []
    
    for svc in services:
        replicas = svc.get("Replicas", "0/0")
        current, desired = replicas.split("/")
        if current == desired and int(current) > 0:
            healthy += 1
        else:
            unhealthy.append(svc.get("Name", "unknown"))
    
    if healthy == total:
        console.print(f"[green]✓ All {total} services healthy[/green]")
        raise SystemExit(0)
    else:
        console.print(f"[red]✗ {len(unhealthy)}/{total} services unhealthy:[/red]")
        for name in unhealthy:
            console.print(f"  - {name}")
        raise SystemExit(1)
