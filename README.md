# Substr8 CLI

[![PyPI version](https://badge.fury.io/py/substr8.svg)](https://pypi.org/project/substr8/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Verifiable AI Infrastructure** — The governance plane for AI agents.

Substr8 provides provable, auditable, and deterministic infrastructure for AI agents. Any framework that can call tools can plug into Substr8.

## What Substr8 Does

| Problem | Substr8 Solution |
|---------|------------------|
| What version of the agent ran? | **FDAA** — File-Driven Agent Architecture |
| Was it allowed to do that? | **ACC** — Agent Capability Control |
| What did it actually do? | **DCT** — Tamper-evident audit ledger |
| What did it remember? | **GAM** — Git-Native Agent Memory |
| Was the conversation valid? | **CIA** — Conversation Integrity Assurance |
| How do I deploy it? | **Gateway** — Docker Swarm orchestration |

## Installation

```bash
pip install substr8
```

## Quick Start

### For Developers (Any Framework)

Connect your LangGraph, AutoGen, PydanticAI, or custom agent to Substr8:

```bash
# Start the MCP governance server
substr8 mcp start --local

# In another terminal, run your agent
python my_agent.py  # calls Substr8 MCP tools
```

Your agent calls MCP tools like `substr8.run.start`, `substr8.policy.check`, `substr8.memory.write`. Every action is policy-checked and logged.

### For Operators (Infrastructure)

Deploy the full Substr8 stack:

```bash
# Start the gateway (OpenClaw runtime + governance services)
substr8 gateway start

# Check status
substr8 gateway status
```

---

## Command Groups

### `substr8 mcp` — MCP Governance Server

The MCP server exposes Substr8 governance as tools any framework can call.

```bash
substr8 mcp start [--local] [--require-auth] [--port 3456]
substr8 mcp stop
substr8 mcp status
substr8 mcp tools           # List available MCP tools
```

**12 MCP Tools:**

| Category | Tools |
|----------|-------|
| RIL (Runtime) | `run.start`, `run.end`, `tool.invoke` |
| ACC (Policy) | `policy.check` |
| DCT (Audit) | `audit.timeline`, `verify.run` |
| GAM (Memory) | `memory.write`, `memory.search` |
| CIA (Integrity) | `cia.status`, `cia.report`, `cia.repairs`, `cia.receipts` |

**With Auth + Rate Limiting:**

```bash
# Require API keys
substr8 mcp start --local --require-auth

# Call with key
curl -H "X-Substr8-Key: sk-substr8-xxx" http://localhost:3456/tools/run/start
```

Rate limits: Free (100/min), Pro (1000/min), Enterprise (10000/min)

---

### `substr8 gateway` — Infrastructure Orchestration

Manages Docker Swarm stacks for the Substr8 runtime.

```bash
substr8 gateway start       # Deploy OpenClaw + FDAA proxy + services
substr8 gateway stop        # Tear down stacks
substr8 gateway status      # Health check all services
substr8 gateway logs        # View service logs
```

**What it deploys:**
- OpenClaw runtime (compiles markdown agents → system prompts)
- FDAA proxy (CIA middleware for LLM calls)
- GAM service (memory with provenance)
- Postgres/pgvector (embeddings)

**When to use:** You're running your own Substr8 infrastructure (self-hosted).

---

### `substr8 dev` — Developer Scaffolding

Creates starter projects and demos.

```bash
substr8 dev init            # Scaffold example agents in current directory
substr8 dev demo            # Run a complete demo (start → agent → audit)
```

**What `dev init` creates:**

```
my-project/
├── examples/
│   ├── langgraph/agent.py
│   ├── pydantic-ai/agent.py
│   └── autogen/agent.py
├── .env.example
└── README.md
```

**When to use:** You're starting a new project that uses Substr8.

---

### `substr8 gam` — Git-Native Agent Memory

Manage agent memory with cryptographic provenance.

```bash
substr8 gam init                    # Initialize repository
substr8 gam remember "text" --tag x # Store a memory
substr8 gam recall "query"          # Search memories
substr8 gam verify <id>             # Verify provenance
substr8 gam status                  # Show status
```

---

### `substr8 fdaa` — Agent Identity & Registry

Package, version, and register agents.

```bash
substr8 fdaa hash <path>            # Hash an agent bundle
substr8 fdaa register <path>        # Register with FDAA registry
substr8 fdaa verify <hash>          # Verify agent integrity
```

---

### `substr8 acc` — Capability Control

Manage agent policies.

```bash
substr8 acc policy list             # List policies
substr8 acc policy check <action>   # Check if action is allowed
```

---

### `substr8 dct` — Audit Ledger

Query tamper-evident logs.

```bash
substr8 dct timeline <run_id>       # Get audit trail
substr8 dct verify <run_id>         # Verify chain integrity
```

---

## How the Commands Work Together

```bash
# 1. OPERATORS: Deploy infrastructure
substr8 gateway start

# 2. DEVELOPERS: Start governance server
substr8 mcp start --local

# 3. DEVELOPERS: Scaffold a new project
mkdir my-agent && cd my-agent
substr8 dev init

# 4. DEVELOPERS: Run your agent
python examples/langgraph/agent.py

# 5. ANYONE: View audit trail
substr8 dct timeline run-abc123
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Your Agent Framework                 │
│              (LangGraph, AutoGen, PydanticAI)           │
└─────────────────────────┬───────────────────────────────┘
                          │ HTTP tool calls
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   Substr8 MCP Server                    │
│                   (substr8 mcp start)                   │
├─────────────────────────────────────────────────────────┤
│  RIL  │  ACC  │  DCT  │  GAM  │  CIA                    │
└───────┴───────┴───────┴───────┴─────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   Substr8 Gateway                       │
│              (substr8 gateway start)                    │
├─────────────────────────────────────────────────────────┤
│  OpenClaw  │  FDAA Proxy  │  Postgres  │  GAM Service   │
└────────────┴──────────────┴────────────┴────────────────┘
```

---

## Hosted Endpoints

| Endpoint | Purpose |
|----------|---------|
| `mcp.substr8labs.com` | Hosted MCP server (coming soon) |
| `fdaa.substr8labs.com` | Agent registry + identity |

---

## Research

| Paper | DOI |
|-------|-----|
| FDAA: File-Driven Agent Architecture | [`10.5281/zenodo.18675147`](https://doi.org/10.5281/zenodo.18675147) |
| ACC: Agent Capability Control | [`10.5281/zenodo.18704577`](https://doi.org/10.5281/zenodo.18704577) |
| GAM: Git-Native Agent Memory | [`10.5281/zenodo.18704573`](https://doi.org/10.5281/zenodo.18704573) |

---

## Links

- **Website:** [substr8labs.com](https://substr8labs.com)
- **Blog:** [substr8labs.substack.com](https://substr8labs.substack.com)
- **GitHub:** [github.com/Substr8-Labs](https://github.com/Substr8-Labs)
- **Twitter:** [@substr8labs](https://twitter.com/substr8labs)

---

## License

MIT — [Substr8 Labs](https://substr8labs.com)

*Frameworks run agents. Substr8 proves what they did.*
