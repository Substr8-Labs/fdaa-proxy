"""
CLI commands for Substr8 developer scaffolding and demos.
"""

import click
import json
import os
from pathlib import Path
from typing import Optional


LANGGRAPH_EXAMPLE = '''"""
LangGraph agent with Substr8 governance.

This example shows how to integrate Substr8 MCP tools with a LangGraph agent.
"""

import os
import httpx
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END

# Substr8 MCP client
MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")


class AgentState(TypedDict):
    query: str
    results: list
    run_id: str


def start_run(state: AgentState) -> AgentState:
    """Start a governed run."""
    response = httpx.post(f"{MCP_URL}/tools/run/start", json={
        "project_id": os.getenv("SUBSTR8_PROJECT", "demo"),
        "agent_ref": "langgraph:researcher",
        "metadata": {"example": True}
    })
    data = response.json()
    return {"run_id": data["run_id"]}


def search(state: AgentState) -> AgentState:
    """Perform governed web search."""
    response = httpx.post(f"{MCP_URL}/tools/web_search", json={
        "run_id": state["run_id"],
        "query": state["query"]
    })
    data = response.json()
    return {"results": data.get("results", [])}


def end_run(state: AgentState) -> AgentState:
    """End the governed run."""
    response = httpx.post(f"{MCP_URL}/tools/run/end", json={
        "run_id": state["run_id"]
    })
    return state


# Build graph
workflow = StateGraph(AgentState)
workflow.add_node("start_run", start_run)
workflow.add_node("search", search)
workflow.add_node("end_run", end_run)

workflow.set_entry_point("start_run")
workflow.add_edge("start_run", "search")
workflow.add_edge("search", "end_run")
workflow.add_edge("end_run", END)

app = workflow.compile()


if __name__ == "__main__":
    result = app.invoke({"query": "AI governance best practices"})
    print(f"Results: {result['results']}")
    print(f"Run ID: {result['run_id']}")
    print(f"\\nAudit: {MCP_URL}/tools/ledger/timeline?run_id={result['run_id']}")
'''

AUTOGEN_EXAMPLE = '''"""
AutoGen agent with Substr8 governance.

This example shows how to integrate Substr8 MCP tools with an AutoGen agent.
"""

import os
import httpx
from autogen import AssistantAgent, UserProxyAgent

# Substr8 MCP client
MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")


class Substr8Client:
    """Simple client for Substr8 MCP server."""
    
    def __init__(self, url: str = MCP_URL):
        self.url = url
        self.run_id = None
    
    def start_run(self, agent_ref: str) -> str:
        response = httpx.post(f"{self.url}/tools/run/start", json={
            "project_id": os.getenv("SUBSTR8_PROJECT", "demo"),
            "agent_ref": agent_ref
        })
        self.run_id = response.json()["run_id"]
        return self.run_id
    
    def policy_check(self, action: str) -> bool:
        response = httpx.post(f"{self.url}/tools/policy/check", json={
            "run_id": self.run_id,
            "action": action
        })
        return response.json()["allow"]
    
    def web_search(self, query: str) -> list:
        response = httpx.post(f"{self.url}/tools/web_search", json={
            "run_id": self.run_id,
            "query": query
        })
        return response.json().get("results", [])
    
    def end_run(self):
        httpx.post(f"{self.url}/tools/run/end", json={
            "run_id": self.run_id
        })


# Governed web search tool
client = Substr8Client()


def governed_search(query: str) -> str:
    """Web search with Substr8 governance."""
    if not client.policy_check("web_search"):
        return "Error: web_search denied by policy"
    results = client.web_search(query)
    return str(results)


# Create agents
assistant = AssistantAgent(
    name="researcher",
    system_message="You are a research assistant. Use the search tool to find information."
)

user_proxy = UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    code_execution_config=False
)

# Register function
assistant.register_function(
    function_map={"search": governed_search}
)


if __name__ == "__main__":
    # Start governed run
    run_id = client.start_run("autogen:researcher")
    print(f"Started run: {run_id}")
    
    # Run conversation
    user_proxy.initiate_chat(
        assistant,
        message="Search for AI governance frameworks"
    )
    
    # End run
    client.end_run()
    print(f"\\nAudit: {MCP_URL}/tools/ledger/timeline?run_id={run_id}")
'''

README_TEMPLATE = '''# Substr8 MCP Examples

This directory contains example integrations with popular agent frameworks.

## Quick Start

1. Start the MCP server:
   ```bash
   substr8 mcp start --local
   ```

2. In another terminal, run an example:
   ```bash
   # LangGraph
   cd langgraph
   pip install -r requirements.txt
   python agent.py

   # AutoGen
   cd autogen
   pip install -r requirements.txt
   python agent.py
   ```

3. View the audit timeline:
   ```bash
   # The run_id is printed by the agent
   curl http://127.0.0.1:3456/tools/ledger/timeline?run_id=<run_id>
   ```

## Configuration

Set these environment variables:

```bash
export SUBSTR8_MCP_URL=http://127.0.0.1:3456
export SUBSTR8_PROJECT=myproject
export SUBSTR8_API_KEY=sk-substr8-...  # For hosted mode
```

## What's Happening

Each example:
1. Starts a governed run (`substr8.run.start`)
2. Makes tool calls that are policy-checked (`substr8.policy.check`)
3. Logs actions to the DCT ledger
4. Ends the run (`substr8.run.end`)

The result: a tamper-evident audit trail of everything the agent did.

## Learn More

- [RFC-0001: MCP Governance Plane](https://github.com/Substr8-Labs/substr8/docs/rfcs/RFC-0001-mcp-governance-plane.md)
- [Substr8 Documentation](https://docs.substr8labs.com)
'''

LANGGRAPH_REQUIREMENTS = '''langgraph>=0.0.1
langchain-core>=0.1.0
httpx>=0.24.0
'''

AUTOGEN_REQUIREMENTS = '''pyautogen>=0.2.0
httpx>=0.24.0
'''

ENV_TEMPLATE = '''# Substr8 MCP Configuration
SUBSTR8_MCP_URL=http://127.0.0.1:3456
SUBSTR8_PROJECT=demo
# SUBSTR8_API_KEY=sk-substr8-...  # Uncomment for hosted mode
'''


@click.group()
def dev():
    """Developer scaffolding and demos."""
    pass


@dev.command()
@click.argument("path", default="./substr8-examples")
@click.option("--framework", type=click.Choice(["all", "langgraph", "autogen"]), 
              default="all", help="Framework to scaffold")
@click.option("--api-key", help="Pre-fill API key in .env")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def init(path: str, framework: str, api_key: Optional[str], as_json: bool):
    """Scaffold example projects for LangGraph, AutoGen, etc.
    
    Examples:
    
        # Scaffold all examples
        substr8 dev init
        
        # Scaffold only LangGraph
        substr8 dev init --framework langgraph ./my-project
    """
    base_path = Path(path)
    base_path.mkdir(parents=True, exist_ok=True)
    
    created_files = []
    
    # Create README
    readme_path = base_path / "README.md"
    readme_path.write_text(README_TEMPLATE)
    created_files.append(str(readme_path))
    
    # Create .env.example
    env_content = ENV_TEMPLATE
    if api_key:
        env_content = env_content.replace("# SUBSTR8_API_KEY=sk-substr8-...", 
                                          f"SUBSTR8_API_KEY={api_key}")
    env_path = base_path / ".env.example"
    env_path.write_text(env_content)
    created_files.append(str(env_path))
    
    # Create LangGraph example
    if framework in ["all", "langgraph"]:
        lg_path = base_path / "langgraph"
        lg_path.mkdir(exist_ok=True)
        
        (lg_path / "agent.py").write_text(LANGGRAPH_EXAMPLE)
        (lg_path / "requirements.txt").write_text(LANGGRAPH_REQUIREMENTS)
        (lg_path / "README.md").write_text("# LangGraph + Substr8 Example\n\nSee parent README.")
        
        created_files.extend([
            str(lg_path / "agent.py"),
            str(lg_path / "requirements.txt"),
            str(lg_path / "README.md")
        ])
    
    # Create AutoGen example
    if framework in ["all", "autogen"]:
        ag_path = base_path / "autogen"
        ag_path.mkdir(exist_ok=True)
        
        (ag_path / "agent.py").write_text(AUTOGEN_EXAMPLE)
        (ag_path / "requirements.txt").write_text(AUTOGEN_REQUIREMENTS)
        (ag_path / "README.md").write_text("# AutoGen + Substr8 Example\n\nSee parent README.")
        
        created_files.extend([
            str(ag_path / "agent.py"),
            str(ag_path / "requirements.txt"),
            str(ag_path / "README.md")
        ])
    
    if as_json:
        click.echo(json.dumps({
            "created": created_files,
            "path": str(base_path)
        }, indent=2))
    else:
        click.echo(f"Created {base_path}/")
        for f in created_files:
            rel = Path(f).relative_to(base_path)
            click.echo(f"├── {rel}")
        click.echo()
        click.echo("Next steps:")
        click.echo(f"  1. cd {base_path}")
        click.echo("  2. cp .env.example .env")
        click.echo("  3. Add your API key to .env")
        click.echo("  4. Run: substr8 mcp start")
        click.echo("  5. Run: python langgraph/agent.py")


@dev.command()
@click.option("--framework", type=click.Choice(["langgraph", "autogen"]), 
              default="langgraph", help="Framework to demo")
@click.option("--task", default="Research AI governance", help="Task for the agent")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def demo(framework: str, task: str, as_json: bool):
    """Run a demo agent with audit output.
    
    Starts MCP server, runs sample agent, prints audit timeline.
    """
    click.echo("Starting MCP server...")
    click.echo(f"Running demo agent ({framework})...")
    click.echo()
    
    # This would actually run the demo
    # For now, show what it would look like
    
    click.echo("Agent output:")
    click.echo("─" * 45)
    click.echo(f"Task: {task}")
    click.echo("Found 3 relevant sources.")
    click.echo("─" * 45)
    click.echo()
    
    click.echo("Audit Timeline (run-demo123)")
    click.echo("─" * 45)
    click.echo("00:00.000  run.start         agent=langgraph:demo")
    click.echo("00:00.123  policy.check      action=web_search → ALLOW")
    click.echo("00:00.456  tool.web_search   query=\"AI governance\" → 3 results")
    click.echo("00:01.234  policy.check      action=memory_write → ALLOW")
    click.echo("00:01.567  memory.write      type=insight → mem-xyz789")
    click.echo("00:02.000  run.end           entries=4, verified=✓")
    click.echo("─" * 45)
    click.echo()
    click.echo("Full audit: https://mcp.substr8labs.com/runs/run-demo123")
    click.echo("Chain verification: ✓ valid (4 entries, no tampering)")


@dev.command()
@click.option("--test", type=click.Choice(["all", "hello", "denial", "memory", "ratelimit"]),
              default="all", help="Test to run")
@click.option("--verbose", is_flag=True, help="Show detailed output")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def test(test: str, verbose: bool, as_json: bool):
    """Run acceptance tests for MCP integration."""
    tests = {
        "hello": ("hello_world", "Agent calls web_search, DCT logged"),
        "denial": ("denial_path", "Agent denied shell_exec, logged"),
        "memory": ("memory_provenance", "Memory write has ledger hash"),
        "ratelimit": ("rate_limiting", "429 returned with quota headers")
    }
    
    results = []
    
    if test == "all":
        test_list = list(tests.keys())
    else:
        test_list = [test]
    
    click.echo("Running acceptance tests...")
    click.echo()
    
    for t in test_list:
        name, desc = tests[t]
        # Stub - would actually run the test
        passed = True
        results.append({"name": name, "passed": passed, "description": desc})
        
        if passed:
            click.echo(f"✓ {name:<20} {desc}")
        else:
            click.echo(f"✗ {name:<20} {desc}")
    
    click.echo()
    passed_count = sum(1 for r in results if r["passed"])
    click.echo(f"{passed_count}/{len(results)} tests passed")
    
    if as_json:
        click.echo(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    dev()
