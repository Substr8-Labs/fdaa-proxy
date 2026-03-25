"""
Substr8 Sandbox CLI - NemoClaw/OpenShell wrapper

Provides unified CLI for sandbox management, wrapping OpenShell commands
with Substr8 conventions and WSL2 compatibility fixes.
"""

import subprocess
import shutil
import os
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

console = Console()

# Default images needed for k3s bootstrap (WSL2 workaround)
BOOTSTRAP_IMAGES = [
    ("docker.io/rancher/mirrored-pause:3.6", "pause"),
    ("docker.io/rancher/mirrored-coredns-coredns:1.14.1", "coredns"),
    ("docker.io/rancher/mirrored-metrics-server:v0.8.1", "metrics-server"),
    ("docker.io/rancher/local-path-provisioner:v0.0.34", "local-path-provisioner"),
    ("docker.io/rancher/klipper-helm:v0.9.14-build20260210", "klipper-helm"),
    ("docker.io/rancher/mirrored-library-busybox:1.37.0", "busybox"),
    ("registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.1.0", "agent-sandbox-controller"),
    ("ghcr.io/nvidia/openshell/gateway:0.0.9", "gateway"),
]


def find_openshell() -> Optional[str]:
    """Find openshell binary."""
    # Check common locations
    locations = [
        shutil.which("openshell"),
        os.path.expanduser("~/.local/bin/openshell"),
        "/usr/local/bin/openshell",
    ]
    for loc in locations:
        if loc and os.path.isfile(loc):
            return loc
    return None


def find_crane() -> Optional[str]:
    """Find crane binary (for image seeding)."""
    locations = [
        shutil.which("crane"),
        os.path.expanduser("~/.local/bin/crane"),
        "/usr/local/bin/crane",
    ]
    for loc in locations:
        if loc and os.path.isfile(loc):
            return loc
    return None


def run_openshell(args: list, capture: bool = False) -> subprocess.CompletedProcess:
    """Run openshell command."""
    openshell = find_openshell()
    if not openshell:
        raise click.ClickException(
            "openshell not found. Install from: https://github.com/NVIDIA/NemoClaw"
        )
    
    cmd = [openshell] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        return subprocess.run(cmd)


@click.group()
def sandbox():
    """NemoClaw/OpenShell sandbox management.
    
    Run AI agents in isolated, sandboxed environments with full
    governance and RunProof capture.
    
    \b
    Examples:
        substr8 sandbox status
        substr8 sandbox start --name nemoclaw --port 8090
        substr8 sandbox seed-images  # WSL2 fix
    """
    pass


@sandbox.command("status")
def sandbox_status():
    """Show sandbox gateway status."""
    openshell = find_openshell()
    if not openshell:
        console.print("[red]✗ openshell not installed[/red]")
        console.print("  Install from: https://github.com/NVIDIA/NemoClaw")
        return
    
    console.print("[bold]Sandbox Status[/bold]\n")
    run_openshell(["status"])


@sandbox.command("start")
@click.option("--name", default="nemoclaw", help="Gateway name")
@click.option("--port", default=8090, help="Gateway port")
@click.option("--seed-images", is_flag=True, help="Pre-seed images (WSL2 fix)")
def sandbox_start(name: str, port: int, seed_images: bool):
    """Start a sandbox gateway.
    
    Creates an isolated k3s environment for running agents.
    Use --seed-images on WSL2 to fix Docker Hub timeout issues.
    """
    console.print(f"[bold]Starting sandbox '{name}' on port {port}...[/bold]\n")
    
    if seed_images:
        console.print("[dim]Pre-seeding images for WSL2 compatibility...[/dim]")
        ctx = click.Context(sandbox_seed_images)
        ctx.invoke(sandbox_seed_images, gateway_name=name)
    
    result = run_openshell(["gateway", "start", "--name", name, "--port", str(port)])
    
    if result.returncode == 0:
        console.print(f"\n[green]✓ Sandbox '{name}' started[/green]")
    else:
        console.print(f"\n[red]✗ Failed to start sandbox[/red]")


@sandbox.command("stop")
@click.option("--name", default="nemoclaw", help="Gateway name")
def sandbox_stop(name: str):
    """Stop a sandbox gateway."""
    console.print(f"[bold]Stopping sandbox '{name}'...[/bold]\n")
    run_openshell(["gateway", "stop", "-g", name])


@sandbox.command("destroy")
@click.option("--name", default="nemoclaw", help="Gateway name")
@click.confirmation_option(prompt="This will delete all sandbox state. Continue?")
def sandbox_destroy(name: str):
    """Destroy a sandbox gateway and all its state."""
    console.print(f"[bold]Destroying sandbox '{name}'...[/bold]\n")
    run_openshell(["gateway", "destroy", "-g", name])


@sandbox.command("list")
def sandbox_list():
    """List available sandbox gateways."""
    run_openshell(["gateway", "select"])


@sandbox.command("select")
@click.argument("name")
def sandbox_select(name: str):
    """Select active sandbox gateway."""
    run_openshell(["gateway", "select", name])


@sandbox.command("seed-images")
@click.option("--gateway-name", default="nemoclaw", help="Gateway container name")
def sandbox_seed_images(gateway_name: str):
    """Pre-seed k3s images for WSL2 compatibility.
    
    WSL2's nested networking prevents k3s from pulling images.
    This command uses crane to pull images on the host and
    imports them directly into the k3s containerd.
    
    Requires: crane (https://github.com/google/go-containerregistry)
    """
    crane = find_crane()
    if not crane:
        console.print("[red]✗ crane not installed[/red]")
        console.print("  Install with:")
        console.print('  curl -sL "https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz" | tar -xz -C ~/.local/bin/ crane')
        raise click.Abort()
    
    container_name = f"openshell-cluster-{gateway_name}"
    
    # Check if container exists
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    if container_name not in result.stdout:
        console.print(f"[red]✗ Container '{container_name}' not found[/red]")
        console.print("  Start the gateway first with: substr8 sandbox start")
        raise click.Abort()
    
    console.print(f"[bold]Seeding images into {container_name}...[/bold]\n")
    
    table = Table(show_header=True)
    table.add_column("Image", style="dim")
    table.add_column("Status")
    
    for image, short_name in BOOTSTRAP_IMAGES:
        tar_path = f"/tmp/{short_name}.tar"
        
        # Pull with crane
        pull_result = subprocess.run(
            [crane, "pull", "--platform", "linux/amd64", image, tar_path],
            capture_output=True, text=True
        )
        
        if pull_result.returncode != 0:
            table.add_row(short_name, "[red]✗ Pull failed[/red]")
            continue
        
        # Copy to container
        subprocess.run(
            ["docker", "cp", tar_path, f"{container_name}:/tmp/{short_name}.tar"],
            capture_output=True
        )
        
        # Import into containerd
        import_result = subprocess.run(
            ["docker", "exec", container_name, "ctr", "-n", "k8s.io", "images", "import", f"/tmp/{short_name}.tar"],
            capture_output=True, text=True
        )
        
        if import_result.returncode == 0:
            table.add_row(short_name, "[green]✓ Imported[/green]")
        else:
            table.add_row(short_name, "[yellow]⚠ Import warning[/yellow]")
    
    console.print(table)
    console.print("\n[green]✓ Image seeding complete[/green]")
    console.print("[dim]Restart pods with: kubectl delete pods --all -A[/dim]")


@sandbox.command("doctor")
def sandbox_doctor():
    """Diagnose sandbox environment issues."""
    console.print("[bold]Sandbox Environment Check[/bold]\n")
    
    table = Table(show_header=True)
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Details")
    
    # Check openshell
    openshell = find_openshell()
    if openshell:
        result = subprocess.run([openshell, "--version"], capture_output=True, text=True)
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        table.add_row("OpenShell", "[green]✓ Installed[/green]", version)
    else:
        table.add_row("OpenShell", "[red]✗ Missing[/red]", "Install from NVIDIA/NemoClaw")
    
    # Check crane
    crane = find_crane()
    if crane:
        result = subprocess.run([crane, "version"], capture_output=True, text=True)
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        table.add_row("crane", "[green]✓ Installed[/green]", version)
    else:
        table.add_row("crane", "[yellow]⚠ Missing[/yellow]", "Needed for WSL2 image seeding")
    
    # Check Docker
    docker = shutil.which("docker")
    if docker:
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        table.add_row("Docker", "[green]✓ Installed[/green]", version)
    else:
        table.add_row("Docker", "[red]✗ Missing[/red]", "Required for sandbox")
    
    # Check for running gateway
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=openshell-cluster", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        gateways = result.stdout.strip().split("\n")
        table.add_row("Gateway", "[green]✓ Running[/green]", ", ".join(gateways))
    else:
        table.add_row("Gateway", "[dim]○ Not running[/dim]", "Start with: substr8 sandbox start")
    
    console.print(table)
    
    # Run openshell doctor if available
    if openshell:
        console.print("\n[bold]OpenShell Doctor[/bold]\n")
        run_openshell(["doctor", "check"])


@sandbox.command("inference")
@click.argument("action", type=click.Choice(["set", "show"]))
@click.argument("provider", required=False)
@click.option("--api-key", help="API key for the provider")
def sandbox_inference(action: str, provider: Optional[str], api_key: Optional[str]):
    """Configure inference providers for the sandbox.
    
    \b
    Examples:
        substr8 sandbox inference show
        substr8 sandbox inference set anthropic --api-key sk-ant-...
    """
    if action == "show":
        run_openshell(["inference", "list"])
    elif action == "set":
        if not provider:
            raise click.ClickException("Provider name required")
        
        args = ["inference", "set", provider]
        if api_key:
            args.extend(["--api-key", api_key])
        
        run_openshell(args)


# Main entry point for standalone use
main = sandbox
