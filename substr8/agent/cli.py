"""Agent identity CLI commands."""

import os
import sys
import json
import click
from pathlib import Path

from .manifest import load_manifest, create_manifest_template
from .hash import compute_identity_hash, verify_identity


@click.group()
def agent():
    """Agent identity management commands."""
    pass


@agent.command()
@click.argument("name")
@click.option("--path", "-p", default=".", help="Base path for agent directory")
@click.option("--version", "-v", default="1.0.0", help="Initial version")
def init(name: str, path: str, version: str):
    """Initialize a new agent with template files.
    
    Creates agent directory with:
    - agent.yaml (manifest)
    - SOUL.md (persona)
    - CAPS.md (capabilities)
    - MEMORY.md (memory policy)
    """
    base = Path(path)
    agent_dir = base / "agents" / name
    
    if agent_dir.exists():
        click.echo(f"❌ Agent directory already exists: {agent_dir}", err=True)
        sys.exit(1)
    
    # Create directory
    agent_dir.mkdir(parents=True)
    
    # Create agent.yaml
    manifest_content = create_manifest_template(name, version)
    (agent_dir / "agent.yaml").write_text(manifest_content)
    
    # Create SOUL.md
    soul_content = f"""# {name.replace('-', ' ').title()} - Persona

## Identity

You are {name}, an AI agent.

## Behavior

- Be helpful and accurate
- Follow governance policies
- Maintain audit trail

## Constraints

- Only use allowed tools
- Respect memory policies
"""
    (agent_dir / "SOUL.md").write_text(soul_content)
    
    # Create CAPS.md
    caps_content = f"""# {name.replace('-', ' ').title()} - Capabilities

## Allowed Tools

- `web_search` - Search the web
- `memory_read` - Read from agent memory
- `memory_write` - Write to agent memory

## Denied Tools

- `shell_exec` - Execute shell commands
- `file_write` - Write arbitrary files

## Rationale

This agent is configured for safe information retrieval
without system modification capabilities.
"""
    (agent_dir / "CAPS.md").write_text(caps_content)
    
    # Create MEMORY.md
    memory_content = f"""# {name.replace('-', ' ').title()} - Memory Policy

## Memory Types

- **Episodic**: Conversation history (retained per session)
- **Semantic**: Learned facts (persisted to GAM)
- **Procedural**: Task patterns (optional)

## Retention Policy

- Session memory: 24 hours
- Long-term memory: Indefinite with versioning

## Privacy

- No PII storage without explicit consent
- Memory provenance tracked via CIA ledger
"""
    (agent_dir / "MEMORY.md").write_text(memory_content)
    
    click.echo(f"✅ Created agent: {name}")
    click.echo(f"\nFiles created:")
    click.echo(f"  {agent_dir}/")
    click.echo(f"  ├── agent.yaml")
    click.echo(f"  ├── SOUL.md")
    click.echo(f"  ├── CAPS.md")
    click.echo(f"  └── MEMORY.md")
    click.echo(f"\nNext steps:")
    click.echo(f"  1. Edit the files to define your agent")
    click.echo(f"  2. Run: substr8 agent hash {agent_dir}")
    click.echo(f"  3. Run: substr8 agent register {agent_dir}")


@agent.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def hash(path: str, as_json: bool):
    """Compute identity hash for an agent.
    
    PATH is the agent directory or agent.yaml file.
    """
    try:
        result = compute_identity_hash(Path(path))
        
        if as_json:
            click.echo(json.dumps(result.to_dict(), indent=2))
        else:
            click.echo(f"\n🔐 Agent Identity Hash")
            click.echo(f"{'='*50}")
            click.echo(f"Agent:         {result.agent_name} v{result.agent_version}")
            click.echo(f"Identity Hash: {result.identity_hash}")
            click.echo(f"Manifest Hash: {result.manifest_hash}")
            click.echo(f"\nFiles included:")
            for f in result.files:
                click.echo(f"  • {f.path}")
                click.echo(f"    {f.hash} ({f.size} bytes)")
            
    except FileNotFoundError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"❌ Invalid manifest: {e}", err=True)
        sys.exit(1)


@agent.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--registry", "-r", default="http://localhost:8099", help="Registry URL")
@click.option("--framework", "-f", default="custom", help="Runtime framework")
@click.option("--publisher", "-p", help="Publisher name")
@click.option("--org", "-o", help="Organization")
def register(path: str, registry: str, framework: str, publisher: str, org: str):
    """Register an agent with the TowerHQ registry.
    
    PATH is the agent directory or agent.yaml file.
    """
    from .registry import RegistryClient
    
    try:
        identity = compute_identity_hash(Path(path))
        manifest = load_manifest(Path(path))
        
        click.echo(f"\n📝 Registering Agent")
        click.echo(f"{'='*50}")
        click.echo(f"Agent:         {identity.agent_name} v{identity.agent_version}")
        click.echo(f"Identity Hash: {identity.identity_hash}")
        click.echo(f"Registry:      {registry}")
        
        # Use manifest values as defaults
        fw = framework if framework != "custom" else manifest.runtime.framework
        pub = publisher or manifest.metadata.author
        organization = org or manifest.metadata.org
        
        client = RegistryClient(registry)
        result = client.register(
            identity=identity,
            framework=fw,
            publisher_name=pub,
            publisher_org=organization,
            description=manifest.metadata.description
        )
        
        if result.success:
            click.echo(f"\n✅ Registered successfully")
            click.echo(f"   Registered at: {result.registered_at}")
        else:
            click.echo(f"\n❌ Registration failed: {result.error}", err=True)
            sys.exit(1)
        
    except FileNotFoundError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"❌ Invalid manifest: {e}", err=True)
        sys.exit(1)


@agent.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--registry", "-r", default="http://localhost:8099", help="Registry URL")
@click.option("--expected", "-e", help="Expected identity hash (skips registry lookup)")
def verify(path: str, registry: str, expected: str):
    """Verify an agent matches its registered identity.
    
    PATH is the local agent directory.
    
    If --expected is provided, verifies against that hash.
    Otherwise, looks up the agent in the registry.
    """
    from .registry import RegistryClient
    
    try:
        # Compute local hash
        identity = compute_identity_hash(Path(path))
        
        click.echo(f"\n🔍 Verifying Agent Identity")
        click.echo(f"{'='*50}")
        click.echo(f"Agent:      {identity.agent_name} v{identity.agent_version}")
        click.echo(f"Local Hash: {identity.identity_hash}")
        
        if expected:
            # Direct hash comparison
            if identity.identity_hash == expected:
                click.echo(f"\n✅ Identity verified - matches expected hash")
            else:
                click.echo(f"\nExpected:   {expected}")
                click.echo(f"\n❌ Identity mismatch!")
                sys.exit(1)
        else:
            # Query registry
            click.echo(f"Registry:   {registry}")
            client = RegistryClient(registry)
            
            # First try to verify the hash
            result = client.verify(identity.identity_hash)
            
            if result.verified:
                click.echo(f"\n✅ Identity verified against registry")
                click.echo(f"   Registered: {result.agent_name} v{result.version}")
                click.echo(f"   Status:     {result.status}")
                click.echo(f"   Date:       {result.registered_at}")
            else:
                # Hash not found - check if agent exists with different hash
                agent_data = client.get_agent(identity.agent_name)
                if agent_data and agent_data.get('latest_hash'):
                    click.echo(f"\n⚠️  Agent exists but hash differs!")
                    click.echo(f"   Registry Hash: {agent_data['latest_hash']}")
                    click.echo(f"   Local Hash:    {identity.identity_hash}")
                    click.echo(f"\n   Local agent has been modified since registration.")
                    sys.exit(1)
                else:
                    click.echo(f"\n⚠️  Agent not found in registry")
                    click.echo(f"   Run: substr8 agent register {path}")
            
    except FileNotFoundError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"❌ Invalid manifest: {e}", err=True)
        sys.exit(1)


@agent.command()
@click.argument("path", type=click.Path(exists=True))
def show(path: str):
    """Show agent manifest details.
    
    PATH is the agent directory or agent.yaml file.
    """
    try:
        manifest = load_manifest(Path(path))
        
        click.echo(f"\n📋 Agent Manifest")
        click.echo(f"{'='*50}")
        click.echo(f"Name:      {manifest.name}")
        click.echo(f"Version:   {manifest.version}")
        click.echo(f"Framework: {manifest.runtime.framework}")
        
        click.echo(f"\nIdentity Files:")
        if manifest.identity.persona:
            click.echo(f"  • Persona: {manifest.identity.persona}")
        if manifest.identity.capabilities:
            click.echo(f"  • Capabilities: {manifest.identity.capabilities}")
        if manifest.identity.memory_policy:
            click.echo(f"  • Memory Policy: {manifest.identity.memory_policy}")
        
        if manifest.governance.allowed_tools:
            click.echo(f"\nAllowed Tools:")
            for tool in manifest.governance.allowed_tools:
                click.echo(f"  ✓ {tool}")
        
        if manifest.governance.denied_tools:
            click.echo(f"\nDenied Tools:")
            for tool in manifest.governance.denied_tools:
                click.echo(f"  ✗ {tool}")
        
        if manifest.metadata.author or manifest.metadata.org:
            click.echo(f"\nMetadata:")
            if manifest.metadata.author:
                click.echo(f"  Author: {manifest.metadata.author}")
            if manifest.metadata.org:
                click.echo(f"  Org: {manifest.metadata.org}")
            if manifest.metadata.tags:
                click.echo(f"  Tags: {', '.join(manifest.metadata.tags)}")
                
    except FileNotFoundError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"❌ Invalid manifest: {e}", err=True)
        sys.exit(1)


@agent.command("list")
@click.option("--registry", "-r", default="http://localhost:8099", help="Registry URL")
@click.option("--org", "-o", help="Filter by organization")
@click.option("--limit", "-l", default=20, help="Max results")
def list_agents(registry: str, org: str, limit: int):
    """List registered agents in the registry."""
    from .registry import RegistryClient
    
    client = RegistryClient(registry)
    agents = client.list_agents(org=org, limit=limit)
    
    if not agents:
        click.echo("No agents registered")
        return
    
    click.echo(f"\n📋 Registered Agents ({len(agents)})")
    click.echo(f"{'='*60}")
    
    for a in agents:
        click.echo(f"\n  {a['name']}")
        if a.get('latest_version'):
            click.echo(f"    Version: {a['latest_version']}")
        if a.get('latest_hash'):
            click.echo(f"    Hash:    {a['latest_hash'][:40]}...")
        if a.get('org'):
            click.echo(f"    Org:     {a['org']}")


@agent.command()
@click.option("--registry", "-r", default="http://localhost:8099", help="Registry URL")
def stats(registry: str):
    """Show registry statistics."""
    from .registry import RegistryClient
    
    client = RegistryClient(registry)
    s = client.stats()
    
    click.echo(f"\n📊 Registry Statistics")
    click.echo(f"{'='*40}")
    click.echo(f"  Agents:        {s.get('agents', 0)}")
    click.echo(f"  Versions:      {s.get('versions', 0)}")
    click.echo(f"  Organizations: {s.get('organizations', 0)}")


# ============ Lifecycle Commands ============

RUNPROOF_URL = os.environ.get("RUNPROOF_URL", "http://localhost:8097")


def lifecycle_api_call(method: str, path: str, data: dict = None) -> dict:
    """Make API call to RunProof Builder for lifecycle commands."""
    import urllib.request
    import json as json_module
    
    url = f"{RUNPROOF_URL}{path}"
    
    if data:
        req = urllib.request.Request(
            url,
            data=json_module.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method=method
        )
    else:
        req = urllib.request.Request(url, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json_module.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            return {"error": json_module.loads(error_body).get("detail", error_body)}
        except:
            return {"error": error_body}
    except Exception as e:
        return {"error": str(e)}


@agent.group()
def lifecycle():
    """Agent lifecycle commands (always-on agents)."""
    pass


@lifecycle.command("register")
@click.argument("agent_id")
@click.option("--metadata", "-m", help="JSON metadata")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_register(agent_id: str, metadata: str, as_json: bool):
    """Register an always-on agent.
    
    Example:
        substr8 agent lifecycle register ada
        substr8 agent lifecycle register ada --metadata '{"role": "co-founder"}'
    """
    data = {}
    if metadata:
        data["metadata"] = json.loads(metadata)
    
    result = lifecycle_api_call("POST", f"/v1/agent/{agent_id}/register", data if data else None)
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Agent registered: {agent_id}")
    click.echo(f"   Status: {result.get('status')}")


@lifecycle.command("heartbeat")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_heartbeat(agent_id: str, as_json: bool):
    """Record agent heartbeat.
    
    Example:
        substr8 agent lifecycle heartbeat ada
    """
    result = lifecycle_api_call("POST", f"/v1/agent/{agent_id}/heartbeat")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Heartbeat recorded: {agent_id}")


@lifecycle.command("status")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_status(agent_id: str, as_json: bool):
    """Get agent lifecycle status.
    
    Example:
        substr8 agent lifecycle status ada
    """
    result = lifecycle_api_call("GET", f"/v1/agent/{agent_id}/lifecycle")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    status = result.get("status", "unknown")
    status_style = {"active": "green", "paused": "yellow", "retired": "red"}.get(status, "dim")
    
    click.echo(f"\n📊 Agent Lifecycle: {agent_id}")
    click.echo(f"{'='*40}")
    click.echo(f"  Status:     {status}")
    click.echo(f"  Registered: {result.get('registered_at', 'N/A')}")
    click.echo(f"  Heartbeat:  {result.get('last_heartbeat', 'N/A')}")
    click.echo(f"  Runs:       {result.get('total_runs', 0)}")
    click.echo(f"  Entries:    {result.get('total_entries', 0)}")


@lifecycle.command("pause")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_pause(agent_id: str, as_json: bool):
    """Pause an active agent.
    
    Example:
        substr8 agent lifecycle pause ada
    """
    result = lifecycle_api_call("POST", f"/v1/agent/{agent_id}/pause")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Agent paused: {agent_id}")


@lifecycle.command("activate")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_activate(agent_id: str, as_json: bool):
    """Activate a paused agent.
    
    Example:
        substr8 agent lifecycle activate ada
    """
    result = lifecycle_api_call("POST", f"/v1/agent/{agent_id}/activate")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Agent activated: {agent_id}")


@lifecycle.command("retire")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_retire(agent_id: str, as_json: bool):
    """Retire an agent (preserves history).
    
    Example:
        substr8 agent lifecycle retire old-agent
    """
    result = lifecycle_api_call("POST", f"/v1/agent/{agent_id}/retire")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Agent retired: {agent_id}")


@lifecycle.command("active")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def lifecycle_active(as_json: bool):
    """List active agents.
    
    Example:
        substr8 agent lifecycle active
    """
    result = lifecycle_api_call("GET", "/v1/agents/active")
    
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    
    if "error" in result:
        click.echo(f"❌ Error: {result['error']}", err=True)
        sys.exit(1)
    
    agents = result.get("agents", [])
    
    if not agents:
        click.echo("No active agents")
        return
    
    click.echo(f"\n📋 Active Agents ({len(agents)})")
    click.echo(f"{'='*50}")
    
    for a in agents:
        stale = "⚠️ STALE" if a.get("is_stale") else ""
        click.echo(f"\n  {a['agent_id']} {stale}")
        click.echo(f"    Last heartbeat: {a.get('last_heartbeat', 'N/A')}")
        click.echo(f"    Runs: {a.get('total_runs', 0)}")


# Export for main CLI
__all__ = ["agent"]
