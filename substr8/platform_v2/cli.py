"""
Substr8 Platform v2 CLI — Docker Compose orchestration matching substr8-platform.

Delegates to Makefile and scripts in the substr8-platform repo.
"""

import os
import subprocess
import sys

import click
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

# Default platform root (can be overridden via env var)
PLATFORM_ROOT = os.environ.get(
    "SUBSTR8_PLATFORM_ROOT",
    os.path.expanduser("~/workspace/substr8-platform")
)


def _run_make(command: str, args: list = None) -> int:
    """Run a make command in the platform directory."""
    if not os.path.exists(os.path.join(PLATFORM_ROOT, "Makefile")):
        console.print(f"[red]Error:[/red] substr8-platform not found at {PLATFORM_ROOT}")
        console.print(f"[dim]Set SUBSTR8_PLATFORM_ROOT or clone https://github.com/Substr8-Labs/substr8-platform[/dim]")
        console.print(f"[dim]Then run: cd {PLATFORM_ROOT} && make bootstrap[/dim]")
        return 1
    
    cmd = ["make", "-C", PLATFORM_ROOT, command] + (args or [])
    result = subprocess.run(cmd)
    return result.returncode


@click.group(name="platform-v2")
def platform_v2():
    """Platform v2 — Docker Compose orchestration (substr8-platform).
    
    Manages the Substr8 dev environment using Docker Compose profiles.
    Requires the substr8-platform repo cloned locally.
    
    Example:
        substr8 platform-v2 doctor
        substr8 platform-v2 up
        substr8 platform-v2 smoke
    """
    pass


@platform_v2.command()
def doctor():
    """Check dev environment prerequisites.
    
    Runs the same checks as 'make doctor' in substr8-platform.
    """
    sys.exit(_run_make("doctor"))


@platform_v2.command()
def bootstrap():
    """First-time setup (env, deps, venvs).
    
    Runs the same setup as 'make bootstrap' in substr8-platform.
    """
    sys.exit(_run_make("bootstrap"))


@platform_v2.command()
@click.option("--profile", default="core", help="Compose profile (core, proof, memory, full)")
def up(profile: str):
    """Start platform services.
    
    Defaults to 'core' profile (Neo4j + ThreadHQ).
    """
    if profile == "core":
        sys.exit(_run_make("up"))
    else:
        # Build custom compose command
        if not os.path.exists(os.path.join(PLATFORM_ROOT, "Makefile")):
            console.print(f"[red]Error:[/red] substr8-platform not found at {PLATFORM_ROOT}")
            sys.exit(1)
        cmd = ["docker", "compose", "--profile", profile, "up", "-d"]
        result = subprocess.run(cmd, cwd=PLATFORM_ROOT)
        sys.exit(result.returncode)


@platform_v2.command()
def down():
    """Stop all services."""
    sys.exit(_run_make("down"))


@platform_v2.command()
def restart():
    """Restart core services."""
    sys.exit(_run_make("restart"))


@platform_v2.command()
def smoke():
    """Run platform smoke test."""
    sys.exit(_run_make("smoke"))


@platform_v2.command()
def ps():
    """List running services."""
    sys.exit(_run_make("ps"))


@platform_v2.command()
@click.option("--tail", default=200, help="Number of lines to show")
def logs(tail: int):
    """Tail service logs."""
    sys.exit(_run_make("logs"))


if __name__ == "__main__":
    platform_v2()