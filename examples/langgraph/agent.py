"""
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


def verify_cia():
    """Check CIA status after run."""
    response = httpx.post(f"{MCP_URL}/tools/cia/status", json={})
    data = response.json()
    return data


if __name__ == "__main__":
    result = app.invoke({"query": "AI governance best practices"})
    print(f"Results: {result['results']}")
    print(f"Run ID: {result['run_id']}")
    print(f"\nAudit: {MCP_URL}/tools/ledger/timeline?run_id={result['run_id']}")
    
    # Verify conversation integrity
    cia = verify_cia()
    print(f"\nCIA Status: enabled={cia.get('enabled')}, mode={cia.get('mode')}")
    if cia.get('stats'):
        stats = cia['stats']
        print(f"  Validated: {stats.get('total_validated', 0)}, Repaired: {stats.get('repaired', 0)}")
