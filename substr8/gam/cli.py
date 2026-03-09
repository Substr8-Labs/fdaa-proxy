"""
GAM CLI - Git-Native Agent Memory Command Line Interface

Usage:
    gam init [path]           Initialize GAM repository
    gam remember <content>    Store a new memory
    gam recall <query>        Search memories
    gam verify <id>           Verify memory provenance
    gam forget <id>           Delete a memory
    gam status                Show repository status
"""

import base64
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from .core import (
    GAMRepository,
    MemoryMetadata,
    init_gam,
    open_gam,
)
from .identity import IdentityManager, HAS_CRYPTO
from .permissions import PermissionManager, PermissionLevel, PathPolicy

console = Console()


def find_repo(path: Optional[str] = None) -> GAMRepository:
    """Find and open GAM repository."""
    search = Path(path) if path else Path.cwd()
    
    # Walk up to find .gam directory
    current = search.resolve()
    while current != current.parent:
        if (current / ".gam").exists():
            return open_gam(current)
        current = current.parent
    
    # Check if current dir is a git repo we can init
    if (search / ".git").exists():
        return init_gam(search)
    
    console.print("[red]Not a GAM repository (no .gam found)[/red]")
    console.print("Run 'gam init' to initialize.")
    sys.exit(1)


@click.group()
@click.version_option(version="0.1.0")
def main():
    """GAM - Git-Native Agent Memory"""
    pass


@main.command()
@click.argument("path", default=".", required=False)
def init(path: str):
    """Initialize a GAM repository."""
    target = Path(path).resolve()
    
    if (target / ".gam").exists():
        console.print(f"[yellow]GAM already initialized at {target}[/yellow]")
        return
    
    try:
        repo = init_gam(target)
        console.print(f"[green]✓ GAM initialized at {target}[/green]")
        console.print(f"  Config: {repo.gam_dir / 'config.yaml'}")
    except Exception as e:
        console.print(f"[red]Failed to initialize: {e}[/red]")
        sys.exit(1)


@main.command()
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8090, type=int, help="Port to bind to")
@click.option("--repo-path", default=".", help="GAM repository path")
def serve(host: str, port: int, repo_path: str):
    """Start the GAM HTTP API server."""
    import os
    os.environ["GAM_REPO_PATH"] = repo_path
    
    console.print(Panel(
        f"[bold]GAM API Server[/bold]\n\n"
        f"Endpoint: [cyan]http://{host}:{port}[/cyan]\n"
        f"Repository: {repo_path}\n\n"
        f"POST /remember - Store memory\n"
        f"POST /recall - Search memories\n"
        f"GET /verify/<id> - Verify provenance",
        title="🧠 Starting"
    ))
    
    from .api import run_server
    run_server(host=host, port=port)


@main.command()
@click.argument("content")
@click.option("--title", "-t", help="Memory title")
@click.option("--source", "-s", 
              type=click.Choice(["conversation", "observation", "user", "inferred", "import"]),
              default="user", help="Source type")
@click.option("--confidence", "-c",
              type=click.Choice(["high", "medium", "low"]),
              default="medium", help="Confidence level")
@click.option("--classification",
              type=click.Choice(["private", "shared", "public"]),
              default="private", help="Classification")
@click.option("--tag", "-T", multiple=True, help="Tags (can be repeated)")
@click.option("--related", "-r", multiple=True, help="Related paths (can be repeated)")
@click.option("--permanent", is_flag=True, help="Mark as decay-exempt")
@click.option("--trace-id", envvar="OTEL_TRACE_ID", help="OTEL trace ID for provenance")
@click.option("--span-id", envvar="OTEL_SPAN_ID", help="OTEL span ID for provenance")
def remember(
    content: str,
    title: Optional[str],
    source: str,
    confidence: str,
    classification: str,
    tag: tuple,
    related: tuple,
    permanent: bool,
    trace_id: Optional[str],
    span_id: Optional[str],
):
    """Store a new memory with optional trace context."""
    repo = find_repo()
    
    metadata = MemoryMetadata(
        source=source,
        confidence=confidence,
        classification=classification,
        tags=list(tag),
        related=list(related),
        decay_exempt=permanent,
    )
    
    # Build trace context if provided
    trace_context = None
    if trace_id:
        trace_context = {"trace_id": trace_id}
        if span_id:
            trace_context["span_id"] = span_id
    
    try:
        memory = repo.remember(content, title=title, metadata=metadata, trace_context=trace_context)
        
        trace_info = ""
        if trace_id:
            trace_info = f"\nTrace: [dim]{trace_id[:16]}...[/dim]"
        
        console.print(Panel(
            f"[green]Memory stored successfully[/green]\n\n"
            f"ID: [cyan]{memory.id}[/cyan]\n"
            f"File: {memory.file_path}\n"
            f"Commit: [dim]{memory.commit_sha[:8]}[/dim]{trace_info}",
            title="✓ Remembered"
        ))
    except Exception as e:
        console.print(f"[red]Failed to remember: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Maximum results")
@click.option("--classification",
              type=click.Choice(["private", "shared", "public"]),
              help="Filter by classification")
@click.option("--verbose", "-v", is_flag=True, help="Show full content")
@click.option("--semantic", "-s", is_flag=True, help="Use semantic (embedding) search")
def recall(query: str, limit: int, classification: Optional[str], verbose: bool, semantic: bool):
    """Search memories."""
    repo = find_repo()
    
    try:
        if semantic:
            results = repo.recall_semantic(query, limit=limit, classification=classification)
        else:
            results = repo.recall(query, limit=limit, classification=classification)
        
        if not results:
            console.print("[yellow]No memories found matching query.[/yellow]")
            return
        
        table = Table(title=f"Memories matching: {query}")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("File", style="dim")
        table.add_column("Content", max_width=60)
        table.add_column("Source", style="green")
        table.add_column("Created", style="blue")
        
        for mem in results:
            content_preview = mem.content[:100].replace("\n", " ")
            if len(mem.content) > 100:
                content_preview += "..."
            
            table.add_row(
                mem.id[-12:],  # Last 12 chars for readability
                mem.file_path,
                content_preview if not verbose else mem.content[:200],
                mem.metadata.source,
                mem.created_at.strftime("%Y-%m-%d"),
            )
        
        console.print(table)
        console.print(f"\n[dim]Found {len(results)} memories[/dim]")
        
    except Exception as e:
        console.print(f"[red]Failed to recall: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument("memory_id")
def verify(memory_id: str):
    """Verify memory provenance."""
    repo = find_repo()
    
    try:
        result = repo.verify(memory_id)
        
        if result.valid:
            lineage_str = " → ".join(result.lineage[:5])
            if len(result.lineage) > 5:
                lineage_str += f" ... (+{len(result.lineage) - 5} more)"
            
            console.print(Panel(
                f"[green]Memory verified[/green]\n\n"
                f"Commit: [cyan]{result.commit_sha[:8]}[/cyan]\n"
                f"Author: {result.author}\n"
                f"Timestamp: {result.timestamp}\n"
                f"Signature: {'✓ Valid' if result.signature_valid else '○ Not signed'}\n"
                f"Lineage: [dim]{lineage_str}[/dim]",
                title="✓ Verified"
            ))
        else:
            console.print(Panel(
                f"[red]Verification failed[/red]\n\n"
                f"Reason: {result.reason}",
                title="✗ Invalid"
            ))
            
    except Exception as e:
        console.print(f"[red]Verification error: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument("memory_id")
@click.option("--reason", "-r", default="User requested", help="Reason for deletion")
@click.option("--hard", is_flag=True, help="Rewrite history (for PII)")
@click.confirmation_option(prompt="Are you sure you want to forget this memory?")
def forget(memory_id: str, reason: str, hard: bool):
    """Delete a memory."""
    repo = find_repo()
    
    try:
        success = repo.forget(memory_id, reason=reason, hard=hard)
        
        if success:
            console.print(f"[green]✓ Memory {memory_id} forgotten[/green]")
        else:
            console.print(f"[yellow]Memory {memory_id} not found[/yellow]")
            
    except NotImplementedError as e:
        console.print(f"[yellow]{e}[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to forget: {e}[/red]")
        sys.exit(1)


@main.command()
def status():
    """Show GAM repository status."""
    repo = find_repo()
    
    # Count memories
    memory_count = 0
    file_count = 0
    for pattern in ["MEMORY.md", "memory/**/*.md"]:
        for file_path in repo.path.glob(pattern):
            if ".gam" in str(file_path):
                continue
            file_count += 1
            memories = repo._parse_memory_file(file_path)
            memory_count += len(memories)
    
    # Git status
    git_status = "clean" if not repo.repo.is_dirty() else "dirty"
    branch = repo.repo.active_branch.name
    
    # Recent commits
    recent = list(repo.repo.iter_commits(max_count=3))
    
    console.print(Panel(
        f"[cyan]GAM Repository Status[/cyan]\n\n"
        f"Path: {repo.path}\n"
        f"Branch: {branch} ({git_status})\n"
        f"Memories: {memory_count} in {file_count} files\n\n"
        f"[dim]Recent commits:[/dim]",
        title="📦 GAM"
    ))
    
    for commit in recent:
        console.print(f"  [dim]{commit.hexsha[:8]}[/dim] {commit.message.split(chr(10))[0]}")


@main.command()
def reindex():
    """Rebuild all indexes from memory files."""
    repo = find_repo()
    
    try:
        count = repo.rebuild_index()
        console.print(f"[green]✓ Indexed {count} memories[/green]")
    except Exception as e:
        console.print(f"[red]Failed to reindex: {e}[/red]")
        sys.exit(1)


@main.command("import")
@click.argument("pattern", default="memory/*.md")
@click.option("--source", "-s", default="import", help="Source type for imported memories")
@click.option("--dry-run", is_flag=True, help="Show what would be imported")
def import_memories(pattern: str, source: str, dry_run: bool):
    """Import existing markdown files into GAM index.
    
    This indexes files without GAM frontmatter, treating each file
    or each H2 section as a separate memory.
    """
    repo = find_repo()
    
    import re
    from datetime import datetime, timezone
    
    count = 0
    for file_path in repo.path.glob(pattern):
        if ".gam" in str(file_path) or file_path.name.startswith("."):
            continue
        
        content = file_path.read_text()
        rel_path = str(file_path.relative_to(repo.path))
        
        # Split by H2 sections if present
        sections = re.split(r'^## ', content, flags=re.MULTILINE)
        
        if len(sections) > 1:
            # Has H2 sections - index each as separate memory
            # First section is the title/intro
            intro = sections[0].strip()
            
            for i, section in enumerate(sections[1:], 1):
                lines = section.split('\n', 1)
                title = lines[0].strip()
                body = lines[1].strip() if len(lines) > 1 else ""
                
                # Generate deterministic ID from file + section
                section_id = f"{file_path.stem}_{i}"
                memory_id = f"mem_import_{section_id}"
                
                if dry_run:
                    console.print(f"[dim]Would import:[/dim] {memory_id} - {title[:50]}")
                else:
                    # Index in temporal index
                    repo.index.temporal.index_memory(
                        memory_id=memory_id,
                        file_path=rel_path,
                        content=f"## {title}\n{body}",
                        source=source,
                    )
                count += 1
        else:
            # No sections - index whole file
            memory_id = f"mem_import_{file_path.stem}"
            
            if dry_run:
                console.print(f"[dim]Would import:[/dim] {memory_id} - {file_path.name}")
            else:
                repo.index.temporal.index_memory(
                    memory_id=memory_id,
                    file_path=rel_path,
                    content=content,
                    source=source,
                )
            count += 1
    
    if dry_run:
        console.print(f"\n[yellow]Dry run: would import {count} memories[/yellow]")
    else:
        console.print(f"[green]✓ Imported {count} memories[/green]")


@main.command()
@click.argument("memory_id")
def show(memory_id: str):
    """Show a specific memory."""
    repo = find_repo()
    
    # Find the memory
    for pattern in ["MEMORY.md", "memory/**/*.md"]:
        for file_path in repo.path.glob(pattern):
            if ".gam" in str(file_path):
                continue
            memories = repo._parse_memory_file(file_path)
            for mem in memories:
                if mem.id == memory_id or mem.id.endswith(memory_id):
                    console.print(Panel(
                        Markdown(mem.content),
                        title=f"Memory: {mem.id}",
                        subtitle=f"{mem.file_path} | {mem.metadata.source} | {mem.metadata.confidence}"
                    ))
                    return
    
    console.print(f"[yellow]Memory {memory_id} not found[/yellow]")


@main.group()
def identity():
    """Manage cryptographic identities."""
    pass


@identity.command("init")
@click.option("--passphrase", "-p", prompt=True, hide_input=True,
              confirmation_prompt=True, help="Master seed passphrase")
def identity_init(passphrase: str):
    """Initialize master seed for agent key derivation."""
    if not HAS_CRYPTO:
        console.print("[red]cryptography package required. Install with: pip install cryptography[/red]")
        sys.exit(1)
    
    repo = find_repo()
    manager = IdentityManager(repo.gam_dir)
    
    try:
        manager.init_master_seed(passphrase)
        console.print("[green]✓ Master seed initialized[/green]")
        console.print("[dim]Note: You'll need to provide this passphrase each session.[/dim]")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        sys.exit(1)


@identity.command("create-agent")
@click.argument("name")
@click.option("--index", "-i", default=0, help="Agent index for key derivation")
@click.option("--passphrase", "-p", prompt=True, hide_input=True,
              help="Master seed passphrase")
def identity_create_agent(name: str, index: int, passphrase: str):
    """Create a new agent identity with DID."""
    if not HAS_CRYPTO:
        console.print("[red]cryptography package required. Install with: pip install cryptography[/red]")
        sys.exit(1)
    
    repo = find_repo()
    manager = IdentityManager(repo.gam_dir)
    manager.init_master_seed(passphrase)
    
    try:
        agent = manager.create_agent(name, index)
        
        console.print(Panel(
            f"[green]Agent identity created[/green]\n\n"
            f"Name: [cyan]{agent.name}[/cyan]\n"
            f"DID: [dim]{agent.did}[/dim]\n"
            f"Path: {agent.derivation_path}",
            title="🤖 Agent Identity"
        ))
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        sys.exit(1)


@identity.command("list")
def identity_list():
    """List all identities."""
    repo = find_repo()
    manager = IdentityManager(repo.gam_dir)
    
    agents = manager.list_agents()
    human = manager.get_human()
    
    if not agents and not human:
        console.print("[yellow]No identities configured.[/yellow]")
        console.print("Run 'gam identity create-agent <name>' to create an agent identity.")
        return
    
    table = Table(title="Identities")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("ID", style="dim", max_width=50)
    
    if human:
        table.add_row("👤 Human", human.name, f"GPG: {human.key_id}")
    
    for agent in agents:
        table.add_row("🤖 Agent", agent.name, agent.did[:50] + "...")
    
    console.print(table)


@identity.command("register-human")
def identity_register_human():
    """Register human GPG identity."""
    repo = find_repo()
    manager = IdentityManager(repo.gam_dir)
    
    # Try to detect GPG key
    detected = manager.detect_gpg_key()
    
    if detected:
        console.print(f"[green]Detected GPG key:[/green] {detected['key_id']}")
        console.print(f"[dim]{detected['uid']}[/dim]")
        
        if click.confirm("Use this key?"):
            # Parse uid for name and email
            uid = detected['uid']
            name = uid.split("<")[0].strip() if "<" in uid else uid
            email = uid.split("<")[1].rstrip(">") if "<" in uid else ""
            
            human = manager.register_human(
                key_id=detected['key_id'],
                email=email,
                name=name,
                fingerprint=detected['key_id'],
            )
            console.print(f"[green]✓ Registered human identity: {human.name}[/green]")
            return
    
    # Manual entry
    key_id = click.prompt("GPG Key ID")
    name = click.prompt("Name")
    email = click.prompt("Email")
    
    human = manager.register_human(
        key_id=key_id,
        email=email,
        name=name,
        fingerprint=key_id,
    )
    console.print(f"[green]✓ Registered human identity: {human.name}[/green]")


@main.group()
def permissions():
    """Manage path-based permissions."""
    pass


@permissions.command("list")
def permissions_list():
    """List all permission policies."""
    repo = find_repo()
    
    table = Table(title="Permission Policies")
    table.add_column("Pattern", style="cyan")
    table.add_column("Permission", style="green")
    table.add_column("Description", style="dim", max_width=40)
    
    for policy in repo.permissions.config.policies:
        perm_color = {
            PermissionLevel.HUMAN_SIGN: "red",
            PermissionLevel.AGENT_SIGN: "yellow",
            PermissionLevel.OPEN: "green",
            PermissionLevel.READONLY: "dim",
        }.get(policy.permission, "white")
        
        table.add_row(
            policy.pattern,
            f"[{perm_color}]{policy.permission.value}[/{perm_color}]",
            policy.description,
        )
    
    console.print(table)
    console.print(f"\n[dim]Default: {repo.permissions.config.default_permission.value}[/dim]")


@permissions.command("check")
@click.argument("path")
def permissions_check(path: str):
    """Check permission for a path."""
    repo = find_repo()
    
    perm = repo.permissions.config.get_permission(path)
    
    perm_info = {
        PermissionLevel.HUMAN_SIGN: ("🔒", "red", "Human GPG signature required"),
        PermissionLevel.AGENT_SIGN: ("🤖", "yellow", "Agent DID signature required"),
        PermissionLevel.OPEN: ("✅", "green", "Open - no signature required"),
        PermissionLevel.READONLY: ("🚫", "dim", "Read-only - no writes allowed"),
    }
    
    emoji, color, desc = perm_info.get(perm, ("?", "white", "Unknown"))
    
    console.print(Panel(
        f"{emoji} [bold {color}]{perm.value.upper()}[/bold {color}]\n\n"
        f"Path: {path}\n"
        f"Meaning: {desc}",
        title="Permission Check"
    ))


@permissions.command("add")
@click.argument("pattern")
@click.option("--level", "-l", 
              type=click.Choice(["open", "agent", "human", "readonly"]),
              required=True, help="Permission level")
@click.option("--description", "-d", default="", help="Policy description")
def permissions_add(pattern: str, level: str, description: str):
    """Add a permission policy."""
    repo = find_repo()
    
    perm_map = {
        "open": PermissionLevel.OPEN,
        "agent": PermissionLevel.AGENT_SIGN,
        "human": PermissionLevel.HUMAN_SIGN,
        "readonly": PermissionLevel.READONLY,
    }
    
    repo.permissions.add_policy(pattern, perm_map[level], description)
    console.print(f"[green]✓ Added policy: {pattern} → {level}[/green]")


@permissions.command("remove")
@click.argument("pattern")
def permissions_remove(pattern: str):
    """Remove a permission policy."""
    repo = find_repo()
    
    if repo.permissions.remove_policy(pattern):
        console.print(f"[green]✓ Removed policy: {pattern}[/green]")
    else:
        console.print(f"[yellow]Policy not found: {pattern}[/yellow]")


@permissions.command("hitl")
def permissions_hitl():
    """Show paths requiring human-in-the-loop signature."""
    repo = find_repo()
    
    hitl_paths = repo.permissions.get_hitl_paths()
    
    if not hitl_paths:
        console.print("[green]No paths require human signature.[/green]")
        return
    
    console.print("[bold red]🔒 Human-in-the-Loop Required:[/bold red]\n")
    for policy in hitl_paths:
        console.print(f"  • {policy.pattern}")
        if policy.description:
            console.print(f"    [dim]{policy.description}[/dim]")


@identity.command("sign")
@click.argument("memory_id")
@click.option("--agent", "-a", help="Agent name to sign with")
@click.option("--passphrase", "-p", help="Master seed passphrase (for agent signing)")
def identity_sign(memory_id: str, agent: Optional[str], passphrase: Optional[str]):
    """Sign a memory with an identity."""
    repo = find_repo()
    manager = IdentityManager(repo.gam_dir)
    
    if agent:
        if not passphrase:
            passphrase = click.prompt("Master seed passphrase", hide_input=True)
        
        manager.init_master_seed(passphrase)
        agent_identity = manager.get_agent(agent)
        
        if not agent_identity:
            console.print(f"[red]Agent '{agent}' not found[/red]")
            sys.exit(1)
        
        # Sign the memory ID as proof
        signature = agent_identity.sign(memory_id.encode())
        sig_b64 = base64.b64encode(signature).decode()
        
        console.print(Panel(
            f"[green]Memory signed[/green]\n\n"
            f"Memory: {memory_id}\n"
            f"Signer: {agent_identity.did[:40]}...\n"
            f"Signature: [dim]{sig_b64[:40]}...[/dim]",
            title="✍️ Signed"
        ))
    else:
        console.print("[yellow]GPG signing not yet implemented in CLI.[/yellow]")
        console.print("Use: git commit -S -m 'message' for GPG-signed commits.")


# === Semantic Search Commands ===

@main.group()
def search():
    """Semantic search commands."""
    pass


@search.command("index")
@click.option("--provider", "-p", 
              type=click.Choice(["auto", "openai", "local"]),
              default="auto", help="Embedding provider")
@click.option("--pattern", default="memory/**/*.md", help="File pattern to index")
@click.option("--include-memory-md", is_flag=True, help="Include MEMORY.md")
def search_index(provider: str, pattern: str, include_memory_md: bool):
    """Build semantic search index from memory files."""
    from .embeddings import GAMSemanticSearch
    
    repo = find_repo()
    
    try:
        searcher = GAMSemanticSearch(repo.gam_dir, provider=provider)
    except ImportError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    
    # Collect memories
    memories = []
    
    if include_memory_md and (repo.path / "MEMORY.md").exists():
        content = (repo.path / "MEMORY.md").read_text()
        chunks = _chunk_markdown(content, "MEMORY.md")
        memories.extend(chunks)
    
    for file_path in repo.path.glob(pattern):
        if ".gam" in str(file_path) or file_path.name.startswith("."):
            continue
        content = file_path.read_text()
        rel_path = str(file_path.relative_to(repo.path))
        chunks = _chunk_markdown(content, rel_path)
        memories.extend(chunks)
    
    if not memories:
        console.print("[yellow]No memory files found to index.[/yellow]")
        return
    
    # Index with progress
    with console.status(f"[bold green]Indexing {len(memories)} chunks..."):
        count = searcher.index_batch(memories)
    
    status = searcher.status()
    console.print(Panel(
        f"[green]✓ Indexed {count} chunks[/green]\n\n"
        f"Embedder: {status['embedder']}\n"
        f"Store: {status['store']}\n"
        f"Total indexed: {status['count']}",
        title="🔍 Search Index"
    ))


@search.command("query")
@click.argument("query")
@click.option("--limit", "-n", default=5, help="Maximum results")
@click.option("--threshold", "-t", default=0.3, help="Minimum similarity (0-1)")
@click.option("--provider", "-p", 
              type=click.Choice(["auto", "openai", "local"]),
              default="auto", help="Embedding provider")
def search_query(query: str, limit: int, threshold: float, provider: str):
    """Search memories semantically."""
    from .embeddings import GAMSemanticSearch
    
    repo = find_repo()
    
    try:
        searcher = GAMSemanticSearch(repo.gam_dir, provider=provider)
    except ImportError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    
    if searcher.count() == 0:
        console.print("[yellow]Search index is empty. Run 'gam search index' first.[/yellow]")
        return
    
    results = searcher.search(query, limit=limit, threshold=threshold)
    
    if not results:
        console.print(f"[yellow]No memories found matching: {query}[/yellow]")
        return
    
    console.print(f"\n[bold]Results for: {query}[/bold]\n")
    
    for i, r in enumerate(results, 1):
        score_color = "green" if r.score > 0.7 else "yellow" if r.score > 0.5 else "dim"
        
        # Truncate content for display
        content_preview = r.content[:200].replace("\n", " ")
        if len(r.content) > 200:
            content_preview += "..."
        
        console.print(Panel(
            f"{content_preview}",
            title=f"[{score_color}]{r.score:.2f}[/{score_color}] {r.file_path or r.memory_id}",
        ))


@search.command("status")
def search_status():
    """Show search index status."""
    from .embeddings import GAMSemanticSearch, HAS_OPENAI, HAS_CHROMADB, HAS_NUMPY
    
    repo = find_repo()
    
    # Show available providers
    console.print("[bold]Available Components:[/bold]\n")
    
    table = Table()
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Notes", style="dim")
    
    import os
    openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    
    table.add_row(
        "OpenAI Embeddings",
        "✓ Available" if (HAS_OPENAI and openai_key) else "○ Not configured",
        "pip install openai + OPENAI_API_KEY" if not HAS_OPENAI else ("Set OPENAI_API_KEY" if not openai_key else "text-embedding-3-small")
    )
    
    try:
        from sentence_transformers import SentenceTransformer
        has_st = True
    except ImportError:
        has_st = False
    
    table.add_row(
        "Local Embeddings",
        "✓ Available" if has_st else "○ Not installed",
        "pip install sentence-transformers" if not has_st else "all-MiniLM-L6-v2"
    )
    
    table.add_row(
        "ChromaDB",
        "✓ Available" if HAS_CHROMADB else "○ Not installed",
        "pip install chromadb" if not HAS_CHROMADB else "Recommended vector store"
    )
    
    table.add_row(
        "NumPy Store",
        "✓ Available" if HAS_NUMPY else "○ Not installed",
        "pip install numpy" if not HAS_NUMPY else "Basic vector store"
    )
    
    console.print(table)
    
    # Show current index
    try:
        searcher = GAMSemanticSearch(repo.gam_dir)
        status = searcher.status()
        
        console.print(f"\n[bold]Current Index:[/bold]")
        console.print(f"  Embedder: {status['embedder']}")
        console.print(f"  Store: {status['store']}")
        console.print(f"  Indexed: {status['count']} chunks")
    except Exception as e:
        console.print(f"\n[yellow]No index initialized: {e}[/yellow]")


def _chunk_markdown(content: str, file_path: str) -> list[tuple[str, str, str, dict]]:
    """
    Chunk markdown content by H2 sections.
    
    Returns list of (memory_id, content, file_path, metadata) tuples.
    """
    import hashlib
    import re
    
    chunks = []
    
    # Split by H2 headers
    sections = re.split(r'^## ', content, flags=re.MULTILINE)
    
    if len(sections) > 1:
        # Has H2 sections
        for i, section in enumerate(sections[1:], 1):
            lines = section.split('\n', 1)
            title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            
            full_content = f"## {title}\n{body}"
            content_hash = hashlib.sha256(full_content.encode()).hexdigest()[:12]
            memory_id = f"{file_path}:{i}:{content_hash}"
            
            chunks.append((
                memory_id,
                full_content,
                file_path,
                {"section": title, "section_index": i}
            ))
    else:
        # No H2 sections - index whole file
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        memory_id = f"{file_path}:0:{content_hash}"
        
        chunks.append((
            memory_id,
            content,
            file_path,
            {}
        ))
    
    return chunks


# === Branch Management Commands ===

@main.group()
def branch():
    """Manage GAM branches (hierarchy)."""
    pass


@branch.command("create")
@click.argument("name")
@click.option("--level", "-l",
              type=click.Choice(["csuite", "project", "feature"]),
              required=True, help="Branch level")
@click.option("--description", "-d", default="", help="Branch description")
@click.option("--parent", "-p", help="Override parent branch")
@click.option("--project", help="For feature branches, the parent project")
def branch_create(name: str, level: str, description: str, parent: str, project: str):
    """Create a new branch in the hierarchy."""
    from .branches import BranchManager, BranchLevel
    
    repo = find_repo()
    manager = BranchManager(repo)
    
    level_map = {
        "csuite": BranchLevel.CSUITE,
        "project": BranchLevel.PROJECT,
        "feature": BranchLevel.FEATURE,
    }
    
    try:
        branch_info = manager.create_branch(
            name=name,
            level=level_map[level],
            description=description,
            parent=parent,
            project=project,
        )
        
        console.print(Panel(
            f"[green]Branch created[/green]\n\n"
            f"Name: [cyan]{branch_info.name}[/cyan]\n"
            f"Level: {branch_info.level.value}\n"
            f"Parent: {branch_info.parent or 'none'}\n"
            f"Description: {description or '(none)'}",
            title="🌿 New Branch"
        ))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@branch.command("list")
@click.option("--level", "-l",
              type=click.Choice(["main", "csuite", "project", "feature"]),
              help="Filter by level")
def branch_list(level: str):
    """List all branches."""
    from .branches import BranchManager, BranchLevel
    
    repo = find_repo()
    manager = BranchManager(repo)
    
    level_filter = None
    if level:
        level_map = {
            "main": BranchLevel.MAIN,
            "csuite": BranchLevel.CSUITE,
            "project": BranchLevel.PROJECT,
            "feature": BranchLevel.FEATURE,
        }
        level_filter = level_map[level]
    
    branches = manager.list_branches(level=level_filter)
    current = manager.current_branch()
    
    if not branches:
        console.print("[yellow]No branches found.[/yellow]")
        return
    
    table = Table(title="GAM Branches")
    table.add_column("", style="green", width=2)
    table.add_column("Branch", style="cyan")
    table.add_column("Level", style="blue")
    table.add_column("Parent", style="dim")
    table.add_column("Description", max_width=40)
    
    level_emoji = {
        BranchLevel.MAIN: "🏠",
        BranchLevel.CSUITE: "👔",
        BranchLevel.PROJECT: "📦",
        BranchLevel.FEATURE: "🔧",
    }
    
    for b in branches:
        marker = "●" if b.name == current else ""
        table.add_row(
            marker,
            b.name,
            f"{level_emoji.get(b.level, '')} {b.level.value}",
            b.parent or "",
            b.description[:40] + "..." if len(b.description) > 40 else b.description,
        )
    
    console.print(table)


@branch.command("tree")
def branch_tree():
    """Show branch hierarchy as a tree."""
    from .branches import BranchManager
    
    repo = find_repo()
    manager = BranchManager(repo)
    
    hierarchy = manager.get_hierarchy()
    current = manager.current_branch()
    
    def print_tree(node: dict, prefix: str = "", is_last: bool = True):
        for i, (name, data) in enumerate(node.items()):
            is_final = i == len(node) - 1
            marker = "└── " if is_final else "├── "
            
            # Highlight current branch
            display_name = f"[bold cyan]{name}[/bold cyan]" if name == current else name
            if name == current:
                display_name += " ●"
            
            console.print(f"{prefix}{marker}{display_name}")
            
            if "children" in data and data["children"]:
                new_prefix = prefix + ("    " if is_final else "│   ")
                print_tree(data["children"], new_prefix, is_final)
    
    console.print("\n[bold]🌳 Branch Hierarchy[/bold]\n")
    print_tree(hierarchy)


@branch.command("checkout")
@click.argument("name")
def branch_checkout(name: str):
    """Switch to a branch."""
    from .branches import BranchManager
    
    repo = find_repo()
    manager = BranchManager(repo)
    
    if manager.checkout(name):
        console.print(f"[green]✓ Switched to {name}[/green]")
    else:
        console.print(f"[red]Branch '{name}' not found[/red]")
        sys.exit(1)


@branch.command("archive")
@click.argument("name")
@click.option("--reason", "-r", default="", help="Reason for archiving")
@click.confirmation_option(prompt="Archive this branch?")
def branch_archive(name: str, reason: str):
    """Archive a branch (mark as inactive)."""
    from .branches import BranchManager
    
    repo = find_repo()
    manager = BranchManager(repo)
    
    if manager.archive_branch(name, reason):
        console.print(f"[green]✓ Archived {name}[/green]")
    else:
        console.print(f"[red]Branch '{name}' not found[/red]")
        sys.exit(1)


# === Proposal Commands ===

@main.group()
def proposal():
    """Manage memory proposals (PR model)."""
    pass


@proposal.command("create")
@click.option("--type", "-t", "proposal_type",
              type=click.Choice(["extract", "remember", "forget"]),
              required=True, help="Proposal type")
@click.option("--source", "-s", help="Source branch (for extract)")
@click.option("--target", "-T", required=True, help="Target branch")
@click.option("--title", required=True, help="Proposal title")
@click.option("--tag", multiple=True, help="Filter by tag (for extract)")
@click.option("--classification", "-c", help="Filter by classification")
@click.option("--content", help="Memory content (for remember)")
@click.option("--file", "-f", "file_path", help="File path (for remember)")
@click.option("--memory-id", "-m", multiple=True, help="Memory IDs (for forget)")
@click.option("--reason", "-r", help="Reason (for forget)")
def proposal_create(
    proposal_type: str,
    source: str,
    target: str,
    title: str,
    tag: tuple,
    classification: str,
    content: str,
    file_path: str,
    memory_id: tuple,
    reason: str,
):
    """Create a new memory proposal."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    try:
        if proposal_type == "extract":
            if not source:
                console.print("[red]--source required for extract proposals[/red]")
                sys.exit(1)
            
            proposal = manager.create_extract_proposal(
                source_branch=source,
                target_branch=target,
                title=title,
                tags=list(tag) if tag else None,
                classification=classification,
            )
        elif proposal_type == "remember":
            if not content:
                console.print("[red]--content required for remember proposals[/red]")
                sys.exit(1)
            
            proposal = manager.create_remember_proposal(
                target_branch=target,
                content=content,
                title=title,
                file_path=file_path or "memory/proposed.md",
                tags=list(tag) if tag else None,
                classification=classification or "private",
            )
        elif proposal_type == "forget":
            if not memory_id:
                console.print("[red]--memory-id required for forget proposals[/red]")
                sys.exit(1)
            if not reason:
                console.print("[red]--reason required for forget proposals[/red]")
                sys.exit(1)
            
            proposal = manager.create_forget_proposal(
                target_branch=target,
                memory_ids=list(memory_id),
                title=title,
                reason=reason,
            )
        else:
            console.print(f"[red]Unknown proposal type: {proposal_type}[/red]")
            sys.exit(1)
        
        console.print(Panel(
            f"[green]Proposal created[/green]\n\n"
            f"ID: [cyan]{proposal.id}[/cyan]\n"
            f"Type: {proposal.type.value}\n"
            f"Status: {proposal.status.value}\n"
            f"Entries: {len(proposal.entries)}",
            title="📝 Memory Proposal"
        ))
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@proposal.command("list")
@click.option("--status", "-s",
              type=click.Choice(["draft", "pending", "approved", "rejected", "merged"]),
              help="Filter by status")
@click.option("--target", "-t", help="Filter by target branch")
def proposal_list(status: str, target: str):
    """List memory proposals."""
    from .proposals import ProposalManager, ProposalStatus
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    status_filter = ProposalStatus(status) if status else None
    proposals = manager.list_proposals(status=status_filter, target_branch=target)
    
    if not proposals:
        console.print("[yellow]No proposals found.[/yellow]")
        return
    
    table = Table(title="Memory Proposals")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="blue")
    table.add_column("Title", max_width=30)
    table.add_column("Target", style="green")
    table.add_column("Status")
    table.add_column("Entries", justify="right")
    
    status_color = {
        "draft": "dim",
        "pending": "yellow",
        "approved": "green",
        "rejected": "red",
        "merged": "cyan",
    }
    
    for p in proposals:
        color = status_color.get(p.status.value, "white")
        table.add_row(
            p.id[-20:],
            p.type.value,
            p.title[:30],
            p.target_branch,
            f"[{color}]{p.status.value}[/{color}]",
            str(len(p.entries)),
        )
    
    console.print(table)


@proposal.command("show")
@click.argument("proposal_id")
def proposal_show(proposal_id: str):
    """Show details of a proposal."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    # Find by partial ID
    proposals = manager.list_proposals()
    matching = [p for p in proposals if proposal_id in p.id]
    
    if not matching:
        console.print(f"[red]Proposal not found: {proposal_id}[/red]")
        sys.exit(1)
    
    proposal = matching[0]
    
    console.print(Panel(
        f"[bold]{proposal.title}[/bold]\n\n"
        f"ID: [cyan]{proposal.id}[/cyan]\n"
        f"Type: {proposal.type.value}\n"
        f"Status: {proposal.status.value}\n"
        f"Source: {proposal.source_branch or '(none)'}\n"
        f"Target: {proposal.target_branch}\n"
        f"Created: {proposal.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"Entries: {len(proposal.entries)}",
        title="📝 Proposal Details"
    ))
    
    if proposal.entries:
        console.print("\n[bold]Entries:[/bold]\n")
        for i, entry in enumerate(proposal.entries):
            status = "[red]REDACTED[/red]" if entry.redacted else "[green]included[/green]"
            preview = entry.content[:100].replace("\n", " ")
            if len(entry.content) > 100:
                preview += "..."
            
            console.print(f"  [{i}] {status} {entry.file_path}")
            if entry.section:
                console.print(f"      Section: {entry.section}")
            console.print(f"      [dim]{preview}[/dim]\n")


@proposal.command("submit")
@click.argument("proposal_id")
def proposal_submit(proposal_id: str):
    """Submit a draft proposal for review."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    try:
        # Find by partial ID
        proposals = manager.list_proposals()
        matching = [p for p in proposals if proposal_id in p.id]
        
        if not matching:
            console.print(f"[red]Proposal not found: {proposal_id}[/red]")
            sys.exit(1)
        
        proposal = manager.submit(matching[0].id)
        console.print(f"[green]✓ Proposal submitted for review[/green]")
        console.print(f"  Status: [yellow]{proposal.status.value}[/yellow]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@proposal.command("approve")
@click.argument("proposal_id")
@click.option("--notes", "-n", default="", help="Review notes")
def proposal_approve(proposal_id: str, notes: str):
    """Approve a pending proposal."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    try:
        proposals = manager.list_proposals()
        matching = [p for p in proposals if proposal_id in p.id]
        
        if not matching:
            console.print(f"[red]Proposal not found: {proposal_id}[/red]")
            sys.exit(1)
        
        proposal = manager.approve(matching[0].id, reviewed_by="human", notes=notes)
        console.print(f"[green]✓ Proposal approved[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@proposal.command("reject")
@click.argument("proposal_id")
@click.option("--reason", "-r", required=True, help="Rejection reason")
def proposal_reject(proposal_id: str, reason: str):
    """Reject a pending proposal."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    try:
        proposals = manager.list_proposals()
        matching = [p for p in proposals if proposal_id in p.id]
        
        if not matching:
            console.print(f"[red]Proposal not found: {proposal_id}[/red]")
            sys.exit(1)
        
        proposal = manager.reject(matching[0].id, reviewed_by="human", reason=reason)
        console.print(f"[yellow]✓ Proposal rejected[/yellow]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@proposal.command("merge")
@click.argument("proposal_id")
@click.confirmation_option(prompt="Merge this proposal?")
def proposal_merge(proposal_id: str):
    """Merge an approved proposal."""
    from .proposals import ProposalManager
    
    repo = find_repo()
    manager = ProposalManager(repo)
    
    try:
        proposals = manager.list_proposals()
        matching = [p for p in proposals if proposal_id in p.id]
        
        if not matching:
            console.print(f"[red]Proposal not found: {proposal_id}[/red]")
            sys.exit(1)
        
        proposal = manager.merge(matching[0].id)
        console.print(Panel(
            f"[green]Proposal merged[/green]\n\n"
            f"Commit: [cyan]{proposal.commit_sha[:12]}[/cyan]\n"
            f"Entries applied: {len([e for e in proposal.entries if not e.redacted])}",
            title="✓ Merged"
        ))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


# === Database Commands ===

@main.group()
def db():
    """Database operations (Postgres + pgvector)."""
    pass


@db.command("migrate")
@click.option("--url", envvar="GAM_DATABASE_URL", help="Database URL")
def db_migrate(url: str):
    """Run database migrations."""
    from .db.migrate import migrate
    
    try:
        migrate(url)
    except Exception as e:
        console.print(f"[red]Migration failed: {e}[/red]")
        sys.exit(1)


@db.command("verify")
@click.option("--url", envvar="GAM_DATABASE_URL", help="Database URL")
def db_verify(url: str):
    """Verify database schema."""
    from .db.migrate import get_connection_string
    import psycopg2
    
    try:
        db_url = url or get_connection_string()
        conn = psycopg2.connect(db_url)
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name LIKE 'gam_%'
            """)
            tables = [t[0] for t in cur.fetchall()]
        
        conn.close()
        
        expected = ["gam_tenants", "gam_branches", "gam_memories", "gam_proposals", "gam_audit_log"]
        missing = set(expected) - set(tables)
        
        if missing:
            console.print(f"[red]Missing tables: {missing}[/red]")
            sys.exit(1)
        
        console.print(f"[green]✓ All {len(expected)} tables present[/green]")
        for t in sorted(tables):
            console.print(f"  • {t}")
            
    except Exception as e:
        console.print(f"[red]Verification failed: {e}[/red]")
        sys.exit(1)


@db.command("serve")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8091, type=int, help="Port to bind to")
def db_serve(host: str, port: int):
    """Start the GAM API server (multi-tenant)."""
    from .api import run_server
    
    console.print(Panel(
        f"[bold]GAM API Server[/bold]\n\n"
        f"Endpoint: [cyan]http://{host}:{port}[/cyan]\n\n"
        f"Endpoints:\n"
        f"  POST   /v1/tenants\n"
        f"  GET    /v1/branches\n"
        f"  POST   /v1/memories/:branch\n"
        f"  POST   /v1/memories/:branch/search\n"
        f"  POST   /v1/proposals\n"
        f"  GET    /v1/audit",
        title="🧠 Starting"
    ))
    
    run_server(host=host, port=port)


@db.command("status")
@click.option("--url", envvar="GAM_DATABASE_URL", help="Database URL")
def db_status(url: str):
    """Show database status and statistics."""
    from .db.migrate import get_connection_string
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    try:
        db_url = url or get_connection_string()
        conn = psycopg2.connect(db_url)
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check pgvector
            cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'")
            vector_ext = cur.fetchone()
            
            # Count tables
            stats = {}
            for table in ["gam_tenants", "gam_branches", "gam_memories", "gam_proposals", "gam_audit_log"]:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = cur.fetchone()["count"]
                except:
                    stats[table] = "N/A"
        
        conn.close()
        
        console.print(Panel(
            f"[bold]Database Status[/bold]\n\n"
            f"pgvector: [green]v{vector_ext['extversion']}[/green]\n\n"
            f"[bold]Table Counts:[/bold]\n"
            f"  • Tenants: {stats.get('gam_tenants', 0)}\n"
            f"  • Branches: {stats.get('gam_branches', 0)}\n"
            f"  • Memories: {stats.get('gam_memories', 0)}\n"
            f"  • Proposals: {stats.get('gam_proposals', 0)}\n"
            f"  • Audit Log: {stats.get('gam_audit_log', 0)}",
            title="🐘 GAM Database"
        ))
        
    except Exception as e:
        console.print(f"[red]Status check failed: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
