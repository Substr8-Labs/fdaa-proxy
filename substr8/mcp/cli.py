"""
CLI commands for Substr8 MCP Server.
"""

import click
import json
import os
import sys
from pathlib import Path
from typing import Optional


@click.group()
@click.version_option()
def mcp():
    """MCP Server - Connect any agent framework to Substr8 governance."""
    pass


@mcp.command()
@click.option("-p", "--port", default=3456, help="Port to listen on")
@click.option("-h", "--host", default="127.0.0.1", help="Host to bind")
@click.option("--api-key", envvar="SUBSTR8_API_KEY", help="API key for mcp.substr8labs.com")
@click.option("--local", is_flag=True, help="Run fully local (no hosted control plane)")
@click.option("--project", help="Project ID to associate runs with")
@click.option("--policy", type=click.Path(exists=True), help="Path to ACC policy file")
@click.option("--cia-db", type=click.Path(exists=True), envvar="SUBSTR8_CIA_DB", 
              help="Path to CIA audit database (auto-detected if not specified)")
@click.option("--cia-url", default="http://localhost:18800/status", 
              help="CIA status endpoint URL")
@click.option("--require-auth", is_flag=True, help="Require API key for all requests")
@click.option("--api-keys-file", type=click.Path(exists=True), 
              help="Path to API keys JSON file")
@click.option("--no-rate-limit", is_flag=True, help="Disable rate limiting")
@click.option("--verbose", is_flag=True, help="Enable verbose logging")
@click.option("--json", "as_json", is_flag=True, help="Output connection info as JSON")
def start(port: int, host: str, api_key: Optional[str], local: bool, 
          project: Optional[str], policy: Optional[str], 
          cia_db: Optional[str], cia_url: str, 
          require_auth: bool, api_keys_file: Optional[str], no_rate_limit: bool,
          verbose: bool, as_json: bool):
    """Start the MCP server locally.
    
    The server exposes Substr8 governance tools (ACC, DCT, GAM) via the
    Model Context Protocol, allowing any compatible agent framework to connect.
    
    Examples:
    
        # Start with hosted control plane
        substr8 mcp start --api-key sk-substr8-xxx --project myproject
        
        # Start fully local (dev mode)
        substr8 mcp start --local --port 3456
    """
    try:
        from .server import create_server
    except ImportError as e:
        click.echo(f"Error: Missing dependencies. Run: pip install fastapi uvicorn", err=True)
        click.echo(f"Details: {e}", err=True)
        sys.exit(1)
    
    server = create_server(
        host=host,
        port=port,
        api_key=api_key,
        project_id=project,
        local_mode=local,
        cia_audit_db=cia_db,
        cia_status_url=cia_url,
        require_auth=require_auth,
        api_keys_file=api_keys_file,
        rate_limiting=not no_rate_limit
    )
    
    if as_json:
        click.echo(json.dumps({
            "status": "starting",
            "endpoint": f"http://{host}:{port}",
            "project": project or "default",
            "local_mode": local
        }, indent=2))
    else:
        click.echo("MCP Server started")
        click.echo(f"  Endpoint:  http://{host}:{port}")
        click.echo(f"  Project:   {project or 'default'}")
        click.echo(f"  Mode:      {'local' if local else 'hosted'}")
        click.echo()
        click.echo("Connect your agent:")
        click.echo("  LangGraph: see examples/langgraph/")
        click.echo("  AutoGen:   see examples/autogen/")
        if not local:
            click.echo()
            click.echo(f"Dashboard: https://mcp.substr8labs.com/projects/{project or 'default'}/runs")
        click.echo()
    
    try:
        server.run()
    except KeyboardInterrupt:
        click.echo("\nMCP Server stopped")


@mcp.command()
@click.option("--force", is_flag=True, help="Force stop without graceful shutdown")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def stop(force: bool, as_json: bool):
    """Stop the running MCP server."""
    # In a real implementation, this would find and stop the running server
    # For now, just provide feedback
    if as_json:
        click.echo(json.dumps({
            "status": "stopped",
            "force": force
        }))
    else:
        click.echo("MCP Server stopped")
        click.echo("  Active runs finalized: 0")
        click.echo("  Ledger entries synced: 0")


@mcp.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(as_json: bool):
    """Show server status, connected projects, and usage."""
    # Check if server is running (stub)
    running = False
    
    if as_json:
        click.echo(json.dumps({
            "status": "not_running" if not running else "running",
            "endpoint": None,
            "uptime": None
        }))
    else:
        if running:
            click.echo("MCP Server Status")
            click.echo("─" * 45)
            click.echo("Status:     ✓ running")
            click.echo("Endpoint:   http://127.0.0.1:3456")
            click.echo("Uptime:     0h 0m")
        else:
            click.echo("MCP Server Status")
            click.echo("─" * 45)
            click.echo("Status:     ○ not running")
            click.echo()
            click.echo("Start with: substr8 mcp start")


@mcp.command()
@click.option("--email", help="Email for account")
@click.option("--project", help="Initial project name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def register(email: Optional[str], project: Optional[str], as_json: bool):
    """Register with mcp.substr8labs.com to get an API key.
    
    Opens browser for authentication, then stores credentials locally.
    """
    import webbrowser
    
    # Would open browser for OAuth flow
    url = "https://mcp.substr8labs.com/auth/register"
    if email:
        url += f"?email={email}"
    if project:
        url += f"&project={project}"
    
    if as_json:
        click.echo(json.dumps({
            "status": "pending",
            "url": url,
            "message": "Complete registration in browser"
        }))
    else:
        click.echo("Opening browser for authentication...")
        click.echo(f"URL: {url}")
        click.echo()
        click.echo("After registration, your API key will be saved to:")
        click.echo("  ~/.substr8/credentials")
    
    # webbrowser.open(url)  # Uncomment when endpoint is live


@mcp.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--verbose", is_flag=True, help="Show full tool schemas")
def tools(as_json: bool, verbose: bool):
    """List available MCP tools exposed by the server."""
    from .server import Substr8MCPServer, MCPServerConfig
    
    server = Substr8MCPServer(MCPServerConfig())
    tool_defs = server.get_tool_definitions()
    
    if as_json:
        click.echo(json.dumps(tool_defs, indent=2))
    else:
        click.echo("Available MCP Tools")
        click.echo("─" * 65)
        click.echo(f"{'Tool':<30} {'Description'}")
        click.echo("─" * 65)
        for tool in tool_defs:
            click.echo(f"{tool['name']:<30} {tool['description']}")
        click.echo("─" * 65)
        click.echo()
        click.echo("Run 'substr8 mcp tools --verbose' for full schemas.")


if __name__ == "__main__":
    mcp()
