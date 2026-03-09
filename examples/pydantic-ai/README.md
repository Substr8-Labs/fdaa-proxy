# PydanticAI + Substr8 Governance

✅ **Validated 2026-03-05** — 8 actions logged, chain verified

This example demonstrates how PydanticAI agents can use Substr8 as a governance layer through MCP tools.

## What This Proves

Any framework that supports tool calling can plug into Substr8:
- LangGraph ✓
- PydanticAI ✓ (this example)
- AutoGen, CrewAI, LlamaIndex → same pattern

## Architecture

```
PydanticAI Agent
      │
      │ tool calls
      ▼
 MCP Server (HTTP)
      │
      ▼
Substr8 Governance Layer
   ├── ACC (policy enforcement)
   ├── DCT (tamper-evident ledger)
   ├── GAM (memory provenance)
   └── RIL (runtime integrity)
```

## Usage

```bash
# Set MCP endpoint
export SUBSTR8_MCP_URL=http://127.0.0.1:3456
export OPENAI_API_KEY=your-key

# Install deps
pip install -r requirements.txt

# Run
python agent.py
```

## Governed Tools

| Tool | Purpose |
|------|---------|
| `start_governed_run` | Begin audited session |
| `governed_web_search` | Search with provenance |
| `governed_memory_write` | Store with lineage |
| `end_governed_run` | Close session, get audit URL |
