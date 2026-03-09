"""
PydanticAI agent with Substr8 governance.

This example shows how to integrate Substr8 MCP tools with a PydanticAI agent.
Proves that any framework with tool calling can use Substr8 as a governance layer.
"""

import os
import httpx
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

# Substr8 MCP endpoint
MCP_URL = os.getenv("SUBSTR8_MCP_URL", "http://127.0.0.1:3456")


@dataclass
class Substr8Context:
    """Context carrying the governed run ID."""
    run_id: str | None = None
    project_id: str = "demo"
    agent_ref: str = "pydantic-ai:researcher"


# Create the agent with Substr8 tools
agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=Substr8Context,
    system_prompt=(
        "You are a research assistant. Use the Substr8 governance tools "
        "to perform searches. Always start a run, do your work, then end the run."
    ),
)


@agent.tool
def start_governed_run(ctx: RunContext[Substr8Context]) -> str:
    """Start a governed Substr8 run. Call this first."""
    response = httpx.post(f"{MCP_URL}/tools/run/start", json={
        "project_id": ctx.deps.project_id,
        "agent_ref": ctx.deps.agent_ref,
        "metadata": {"framework": "pydantic-ai"}
    })
    data = response.json()
    ctx.deps.run_id = data["run_id"]
    return f"Started run: {data['run_id']}"


@agent.tool
def governed_web_search(ctx: RunContext[Substr8Context], query: str) -> str:
    """Search the web through Substr8 governance layer."""
    if not ctx.deps.run_id:
        return "Error: Must start a run first"
    
    response = httpx.post(f"{MCP_URL}/tools/web_search", json={
        "run_id": ctx.deps.run_id,
        "query": query
    })
    data = response.json()
    results = data.get("results", [])
    
    if not results:
        return "No results found"
    
    # Format results
    formatted = []
    for r in results[:5]:
        formatted.append(f"- {r.get('title', 'Untitled')}: {r.get('url', 'N/A')}")
    return "\n".join(formatted)


@agent.tool
def governed_memory_write(
    ctx: RunContext[Substr8Context], 
    content: str, 
    memory_type: str = "fact"
) -> str:
    """Write to governed memory with provenance."""
    if not ctx.deps.run_id:
        return "Error: Must start a run first"
    
    response = httpx.post(f"{MCP_URL}/tools/memory/write", json={
        "run_id": ctx.deps.run_id,
        "content": content,
        "type": memory_type
    })
    data = response.json()
    return f"Memory stored: {data.get('memory_id', 'unknown')}"


@agent.tool
def end_governed_run(ctx: RunContext[Substr8Context]) -> str:
    """End the governed run. Call this when done."""
    if not ctx.deps.run_id:
        return "No active run to end"
    
    response = httpx.post(f"{MCP_URL}/tools/run/end", json={
        "run_id": ctx.deps.run_id
    })
    audit_url = f"{MCP_URL}/tools/ledger/timeline?run_id={ctx.deps.run_id}"
    return f"Run completed. Audit trail: {audit_url}"


@agent.tool
def check_cia_status(ctx: RunContext[Substr8Context]) -> str:
    """Check CIA (Conversation Integrity) status."""
    response = httpx.post(f"{MCP_URL}/tools/cia/status", json={
        "run_id": ctx.deps.run_id
    })
    data = response.json()
    return f"CIA: enabled={data.get('enabled')}, mode={data.get('mode')}, provider={data.get('provider_path')}"


@agent.tool  
def get_cia_receipts(ctx: RunContext[Substr8Context], limit: int = 5) -> str:
    """Get recent LLM call receipts (hashes only, no content)."""
    response = httpx.post(f"{MCP_URL}/tools/cia/receipts", json={
        "run_id": ctx.deps.run_id,
        "limit": limit
    })
    data = response.json()
    receipts = data.get("receipts", [])
    if not receipts:
        return "No receipts found"
    
    lines = [f"Recent LLM calls ({len(receipts)}):"]
    for r in receipts[:3]:
        lines.append(f"  - {r.get('model')}: {r.get('request_sha256', '')[:20]}...")
    return "\n".join(lines)


async def main():
    """Demo: Run a governed research task."""
    context = Substr8Context(
        project_id=os.getenv("SUBSTR8_PROJECT", "demo"),
        agent_ref="pydantic-ai:researcher"
    )
    
    result = await agent.run(
        "Research AI governance frameworks. Start a governed run, search for information, "
        "save a key finding to memory, then end the run.",
        deps=context
    )
    
    print(f"\n{'='*60}")
    print("RESULT:")
    # PydanticAI 0.x uses .output, recent versions may use .data
    print(getattr(result, 'output', getattr(result, 'data', str(result))))
    print(f"\n{'='*60}")
    
    if context.run_id:
        print(f"Run ID: {context.run_id}")
        print(f"Audit: {MCP_URL}/tools/ledger/timeline?run_id={context.run_id}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
