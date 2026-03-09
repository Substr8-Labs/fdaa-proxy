"""
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


def verify_cia():
    """Check CIA status after run."""
    response = httpx.post(f"{MCP_URL}/tools/cia/status", json={})
    return response.json()


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
    print(f"\nAudit: {MCP_URL}/tools/ledger/timeline?run_id={run_id}")
    
    # Verify conversation integrity
    cia = verify_cia()
    print(f"\nCIA Status: enabled={cia.get('enabled')}, mode={cia.get('mode')}")
    if cia.get('stats'):
        stats = cia['stats']
        print(f"  Validated: {stats.get('total_validated', 0)}, Repaired: {stats.get('repaired', 0)}")
