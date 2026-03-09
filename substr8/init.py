"""
Project scaffolding for Substr8.

Creates example projects with working integrations for popular frameworks.
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List


# === Templates ===

LANGGRAPH_AGENT = '''"""
LangGraph agent with Substr8 governance.

Every tool call is policy-checked and logged to a tamper-evident audit trail.
"""

import os
import httpx
from typing import TypedDict
from langgraph.graph import StateGraph, END

MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")
API_KEY = os.getenv("SUBSTR8_API_KEY", "")

HEADERS = {"X-Substr8-Key": API_KEY} if API_KEY else {}


class AgentState(TypedDict):
    query: str
    results: list
    run_id: str


def start_run(state: AgentState) -> AgentState:
    """Start a governed run."""
    response = httpx.post(f"{MCP_URL}/tools/run/start", json={
        "project_id": os.getenv("SUBSTR8_PROJECT", "demo"),
        "agent_ref": "langgraph:researcher",
        "metadata": {"framework": "langgraph"}
    }, headers=HEADERS)
    data = response.json()
    return {"run_id": data["run_id"]}


def search(state: AgentState) -> AgentState:
    """Perform governed web search."""
    response = httpx.post(f"{MCP_URL}/tools/web_search", json={
        "run_id": state["run_id"],
        "query": state["query"]
    }, headers=HEADERS)
    data = response.json()
    return {"results": data.get("result", {}).get("items", [])}


def end_run(state: AgentState) -> AgentState:
    """End the governed run and verify chain."""
    response = httpx.post(f"{MCP_URL}/tools/run/end", json={
        "run_id": state["run_id"]
    }, headers=HEADERS)
    data = response.json()
    print(f"Chain valid: {data.get('chain_valid')}")
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
    print("Starting governed agent run...")
    result = app.invoke({"query": "AI governance best practices"})
    
    print(f"\\nResults: {len(result.get('results', []))} items found")
    print(f"Run ID: {result['run_id']}")
    print(f"\\nAudit timeline: {MCP_URL}/tools/ledger/timeline (POST with run_id)")
'''

PYDANTIC_AI_AGENT = '''"""
PydanticAI agent with Substr8 governance.

Type-safe tools with policy enforcement and audit logging.
"""

import os
import httpx
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")
API_KEY = os.getenv("SUBSTR8_API_KEY", "")

HEADERS = {"X-Substr8-Key": API_KEY} if API_KEY else {}


@dataclass
class Substr8Context:
    """Context carrying the governed run ID."""
    run_id: str | None = None
    project_id: str = "demo"
    agent_ref: str = "pydantic-ai:researcher"


agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=Substr8Context,
    system_prompt=(
        "You are a research assistant. Use the governed tools to search "
        "and store findings. Always start and end runs properly."
    ),
)


@agent.tool
def start_governed_run(ctx: RunContext[Substr8Context]) -> str:
    """Start a governed Substr8 run."""
    response = httpx.post(f"{MCP_URL}/tools/run/start", json={
        "project_id": ctx.deps.project_id,
        "agent_ref": ctx.deps.agent_ref,
        "metadata": {"framework": "pydantic-ai"}
    }, headers=HEADERS)
    data = response.json()
    ctx.deps.run_id = data["run_id"]
    return f"Started run: {data['run_id']}"


@agent.tool
def governed_web_search(ctx: RunContext[Substr8Context], query: str) -> str:
    """Search the web through Substr8 governance."""
    if not ctx.deps.run_id:
        return "Error: Must start a run first"
    
    response = httpx.post(f"{MCP_URL}/tools/web_search", json={
        "run_id": ctx.deps.run_id,
        "query": query
    }, headers=HEADERS)
    data = response.json()
    
    if not data.get("allowed", True):
        return f"Policy denied: {data.get('reason')}"
    
    results = data.get("result", {}).get("items", [])
    return f"Found {len(results)} results"


@agent.tool
def end_governed_run(ctx: RunContext[Substr8Context]) -> str:
    """End the governed run."""
    if not ctx.deps.run_id:
        return "No active run"
    
    response = httpx.post(f"{MCP_URL}/tools/run/end", json={
        "run_id": ctx.deps.run_id
    }, headers=HEADERS)
    data = response.json()
    return f"Run ended. Chain valid: {data.get('chain_valid')}"


async def main():
    context = Substr8Context()
    
    result = await agent.run(
        "Start a run, search for AI governance frameworks, then end the run.",
        deps=context
    )
    
    print(f"\\nResult: {getattr(result, 'output', result)}")
    print(f"Run ID: {context.run_id}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
'''

AUTOGEN_AGENT = '''"""
AutoGen agent with Substr8 governance.

Multi-agent system with governed function calls.
"""

import os
import httpx

MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")
API_KEY = os.getenv("SUBSTR8_API_KEY", "")

HEADERS = {"X-Substr8-Key": API_KEY} if API_KEY else {}


class Substr8Client:
    """Client for Substr8 MCP server."""
    
    def __init__(self):
        self.run_id = None
    
    def start_run(self, agent_ref: str) -> str:
        response = httpx.post(f"{MCP_URL}/tools/run/start", json={
            "project_id": os.getenv("SUBSTR8_PROJECT", "demo"),
            "agent_ref": agent_ref,
            "metadata": {"framework": "autogen"}
        }, headers=HEADERS)
        self.run_id = response.json()["run_id"]
        return self.run_id
    
    def policy_check(self, action: str) -> bool:
        response = httpx.post(f"{MCP_URL}/tools/policy/check", json={
            "run_id": self.run_id,
            "action": action
        }, headers=HEADERS)
        return response.json()["allow"]
    
    def web_search(self, query: str) -> list:
        response = httpx.post(f"{MCP_URL}/tools/web_search", json={
            "run_id": self.run_id,
            "query": query
        }, headers=HEADERS)
        return response.json().get("result", {}).get("items", [])
    
    def end_run(self):
        response = httpx.post(f"{MCP_URL}/tools/run/end", json={
            "run_id": self.run_id
        }, headers=HEADERS)
        return response.json()


client = Substr8Client()


def governed_search(query: str) -> str:
    """Web search with Substr8 governance."""
    if not client.policy_check("web_search"):
        return "Error: web_search denied by policy"
    results = client.web_search(query)
    return f"Found {len(results)} results: {results}"


if __name__ == "__main__":
    # Start governed run
    run_id = client.start_run("autogen:researcher")
    print(f"Started run: {run_id}")
    
    # Simulate agent work
    print("\\nAgent searching...")
    result = governed_search("AI governance frameworks")
    print(f"Result: {result}")
    
    # End run
    end_result = client.end_run()
    print(f"\\nRun ended. Chain valid: {end_result.get('chain_valid')}")
    print(f"Audit: {MCP_URL}/tools/ledger/timeline (POST with run_id: {run_id})")
'''

VERIFY_CIA = '''#!/usr/bin/env python3
"""
Verify Conversation Integrity (CIA) status.

Run this after agent execution to audit LLM interactions.
"""

import os
import httpx

MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")
API_KEY = os.getenv("SUBSTR8_API_KEY", "")

HEADERS = {"X-Substr8-Key": API_KEY} if API_KEY else {}


def check_cia():
    print("=" * 50)
    print("CIA (Conversation Integrity) Status")
    print("=" * 50)
    
    # Status
    resp = httpx.post(f"{MCP_URL}/tools/cia/status", json={}, headers=HEADERS)
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    print(f"  Enabled: {data.get('enabled')}")
    print(f"  Mode: {data.get('mode')}")
    print(f"  Provider: {data.get('provider_path')}")
    
    # Report
    print("\\n" + "=" * 50)
    print("Integrity Report")
    print("=" * 50)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/report", json={}, headers=HEADERS)
    data = resp.json()
    print(f"  Validated: {data.get('total_validated', 0)}")
    print(f"  Repaired: {data.get('repaired', 0)}")
    print(f"  Rejected: {data.get('rejected', 0)}")
    
    # Receipts
    print("\\n" + "=" * 50)
    print("Recent LLM Receipts")
    print("=" * 50)
    
    resp = httpx.post(f"{MCP_URL}/tools/cia/receipts", json={"limit": 3}, headers=HEADERS)
    data = resp.json()
    
    for r in data.get("receipts", [])[:3]:
        print(f"  [{r.get('seq')}] {r.get('model')}")
        print(f"      Hash: {r.get('request_sha256', '')[:40]}...")
    
    print("\\n✓ CIA verification complete")


if __name__ == "__main__":
    check_cia()
'''

README_TEMPLATE = '''# {project_name}

A Substr8-governed agent project.

## Quick Start

1. **Start the MCP server:**
   ```bash
   substr8 mcp start --local
   ```

2. **Run an example agent:**
   ```bash
   # LangGraph
   cd examples/langgraph
   pip install -r requirements.txt
   python agent.py

   # PydanticAI
   cd examples/pydantic-ai
   pip install -r requirements.txt
   python agent.py

   # AutoGen
   cd examples/autogen
   pip install -r requirements.txt
   python agent.py
   ```

3. **Verify conversation integrity:**
   ```bash
   python verify-cia.py
   ```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `SUBSTR8_MCP_URL` | MCP server URL (default: http://127.0.0.1:3456) |
| `SUBSTR8_PROJECT` | Project ID for audit grouping |
| `SUBSTR8_API_KEY` | API key (required for hosted mode) |

## What Substr8 Does

Every agent action is:
- **Policy-checked** — ACC decides if it's allowed
- **Logged** — DCT records it in a tamper-evident ledger
- **Hash-chained** — Modify one entry, the chain breaks

Result: You can prove what your agent did.

## Learn More

- [Substr8 Docs](https://docs.substr8labs.com)
- [MCP Tools Reference](https://docs.substr8labs.com/mcp-tools)
- [Substack](https://substr8labs.substack.com)
'''

ENV_TEMPLATE = '''# Substr8 Configuration
SUBSTR8_MCP_URL=http://127.0.0.1:3456
SUBSTR8_PROJECT=demo
# SUBSTR8_API_KEY=sk-substr8-...  # Required for hosted mode
'''

LANGGRAPH_REQUIREMENTS = '''langgraph>=0.0.1
langchain-core>=0.1.0
httpx>=0.27.0
'''

PYDANTIC_AI_REQUIREMENTS = '''pydantic-ai>=0.0.20
httpx>=0.27.0
'''

AUTOGEN_REQUIREMENTS = '''pyautogen>=0.2.0
httpx>=0.27.0
'''


def scaffold_project(
    path: str = ".",
    framework: str = "all",
    api_key: Optional[str] = None,
    minimal: bool = False
) -> Dict[str, Any]:
    """
    Scaffold a new Substr8 project.
    
    Args:
        path: Directory to create project in
        framework: Which framework(s) to scaffold
        api_key: Pre-fill API key in .env
        minimal: Create minimal structure
    
    Returns:
        {"success": bool, "path": str, "files": list, "error": str}
    """
    try:
        base = Path(path).resolve()
        base.mkdir(parents=True, exist_ok=True)
        
        files_created: List[str] = []
        
        # Project name from directory
        project_name = base.name if base.name != "." else "substr8-project"
        
        # README
        readme_path = base / "README.md"
        readme_path.write_text(README_TEMPLATE.format(project_name=project_name))
        files_created.append("README.md")
        
        # .env.example
        env_content = ENV_TEMPLATE
        if api_key:
            env_content = env_content.replace(
                "# SUBSTR8_API_KEY=sk-substr8-...",
                f"SUBSTR8_API_KEY={api_key}"
            )
        (base / ".env.example").write_text(env_content)
        files_created.append(".env.example")
        
        # verify-cia.py
        (base / "verify-cia.py").write_text(VERIFY_CIA)
        files_created.append("verify-cia.py")
        
        # Examples directory
        examples = base / "examples"
        examples.mkdir(exist_ok=True)
        
        # LangGraph
        if framework in ["all", "langgraph"]:
            lg = examples / "langgraph"
            lg.mkdir(exist_ok=True)
            (lg / "agent.py").write_text(LANGGRAPH_AGENT)
            (lg / "requirements.txt").write_text(LANGGRAPH_REQUIREMENTS)
            files_created.extend([
                "examples/langgraph/agent.py",
                "examples/langgraph/requirements.txt"
            ])
        
        # PydanticAI
        if framework in ["all", "pydantic-ai"]:
            pai = examples / "pydantic-ai"
            pai.mkdir(exist_ok=True)
            (pai / "agent.py").write_text(PYDANTIC_AI_AGENT)
            (pai / "requirements.txt").write_text(PYDANTIC_AI_REQUIREMENTS)
            files_created.extend([
                "examples/pydantic-ai/agent.py",
                "examples/pydantic-ai/requirements.txt"
            ])
        
        # AutoGen
        if framework in ["all", "autogen"]:
            ag = examples / "autogen"
            ag.mkdir(exist_ok=True)
            (ag / "agent.py").write_text(AUTOGEN_AGENT)
            (ag / "requirements.txt").write_text(AUTOGEN_REQUIREMENTS)
            files_created.extend([
                "examples/autogen/agent.py",
                "examples/autogen/requirements.txt"
            ])
        
        # .gitignore
        gitignore = """# Substr8 project
.env
*.pyc
__pycache__/
.venv/
venv/
"""
        (base / ".gitignore").write_text(gitignore)
        files_created.append(".gitignore")
        
        return {
            "success": True,
            "path": str(base),
            "files": files_created,
            "error": None
        }
    
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "files": [],
            "error": str(e)
        }
