# Substr8 MCP Examples

> **Your framework runs agents. Substr8 proves what they did.**

These examples show how any agent framework can plug into Substr8's governance layer to get:

- ✅ **Policy enforcement** (ACC) — What the agent is allowed to do
- ✅ **Audit trail** (DCT) — What the agent actually did
- ✅ **Tamper detection** — Cryptographic proof the trail wasn't modified

## Quick Start (5 minutes)

### 1. Install Substr8

```bash
pip install substr8
```

### 2. Start the MCP Server

```bash
substr8 mcp start --local
```

You'll see:
```
MCP Server started
  Endpoint:  http://127.0.0.1:3456
  Mode:      local
```

### 3. Run an Example Agent

```bash
# LangGraph
cd examples/langgraph && pip install -r requirements.txt
python agent.py

# PydanticAI
cd examples/pydantic-ai && pip install -r requirements.txt
python agent.py

# AutoGen
cd examples/autogen && pip install -r requirements.txt
python agent.py
```

### 4. View the Audit Trail

```bash
curl -s http://127.0.0.1:3456/tools/ledger/timeline \
  -X POST -H "Content-Type: application/json" \
  -d '{"run_id": "YOUR_RUN_ID"}' | jq
```

---

## Framework Validation Results

| Framework | Agent Ref | Actions Logged | Chain Verified |
|-----------|-----------|----------------|----------------|
| LangGraph | `langgraph:researcher` | ✓ | ✓ |
| PydanticAI | `pydantic-ai:researcher` | 8 | ✓ |
| AutoGen | `autogen:researcher` | 3 | ✓ |

**Key insight:** Three independent ecosystems, same governance plane.

---

## Policy Denial Proof

Substr8 logs denied actions too. This is the compliance story:

```json
{
  "agent_ref": "untrusted:agent",
  "entries": [
    {"seq": 0, "action": "file_write", "allowed": true},
    {"seq": 1, "action": "shell_exec", "allowed": false},
    {"seq": 2, "action": "web_search", "allowed": true}
  ],
  "chain_valid": true
}
```

Even when ACC blocks an action:
1. The attempt is recorded in DCT
2. The hash chain remains intact
3. You have a cryptographic receipt of what the agent *tried* to do

That's how you answer: "What was this agent doing?"

---

## Hash Chain Explained

Every entry in the audit trail references the previous entry's hash:

```
seq 0 → prev_hash: 0x000... (genesis)
seq 1 → prev_hash: hash(seq 0) ✓
seq 2 → prev_hash: hash(seq 1) ✓
seq 3 → prev_hash: hash(seq 2) ✓
```

If anyone modifies an entry, the chain breaks. `chain_valid: false`.

---

## MCP Tools Available

```
substr8 mcp tools
```

### Runtime Governance (RIL + ACC + DCT + GAM)

| Tool | Description |
|------|-------------|
| `substr8.run.start` | Create governed run context |
| `substr8.run.end` | Close run, verify chain |
| `substr8.policy.check` | Check if action is allowed (ACC) |
| `substr8.tool.invoke` | Governed tool gateway |
| `substr8.memory.write` | Write memory with provenance (GAM) |
| `substr8.memory.search` | Search memory with provenance |
| `substr8.audit.timeline` | Get audit trail (DCT) |
| `substr8.verify.run` | Verify chain integrity |

### Conversation Integrity (CIA)

| Tool | Description |
|------|-------------|
| `substr8.cia.status` | Is CIA enabled? Mode, version, provider path |
| `substr8.cia.report` | Integrity summary: validated, repaired, rejected |
| `substr8.cia.repairs` | Itemized repair list (hashes only, no content) |
| `substr8.cia.receipts` | LLM call receipts (request/response hashes + model) |

> **Note:** CIA tools expose audit data only. They do not proxy OAuth/subscription traffic.

---

## Architecture

```
Your Agent Framework (LangGraph, PydanticAI, AutoGen, etc.)
         │
         │ HTTP tool calls
         ▼
    Substr8 MCP Server
         │
         ├── RIL (Runtime Integrity Layer)
         ├── ACC (Policy Enforcement)
         ├── DCT (Tamper-Evident Ledger)
         ├── GAM (Memory Provenance)
         └── CIA (Conversation Integrity) ← audit surface
```

**Your framework handles execution logic. Substr8 handles governance.**

You don't change your runtime — you add a trust layer.

---

## CIA: Conversation Integrity Verification

CIA validates tool_use/tool_result pairing in LLM conversations. It runs automatically as middleware — you don't invoke it, but you can audit it.

### Quick Check

```bash
# Is CIA enabled?
curl -s -X POST http://127.0.0.1:3456/tools/cia/status \
  -H "Content-Type: application/json" -d '{}' | jq .
```

```json
{
  "enabled": true,
  "mode": "permissive",
  "cia_version": "1.0.0",
  "provider_path": "subscription",
  "scope": "global"
}
```

### Get LLM Call Receipts

```bash
curl -s -X POST http://127.0.0.1:3456/tools/cia/receipts \
  -H "Content-Type: application/json" -d '{"limit": 5}' | jq .
```

```json
{
  "receipts": [
    {
      "seq": 0,
      "timestamp": "2026-03-04T08:28:16Z",
      "request_sha256": "sha256:59427f52...",
      "response_sha256": "sha256:59427f52...",
      "model": "claude-opus-4-5"
    }
  ]
}
```

**Key insight:** Receipts contain hashes, not content. You can prove an LLM call happened without exposing what was said.

### View Repairs (if any)

```bash
curl -s -X POST http://127.0.0.1:3456/tools/cia/repairs \
  -H "Content-Type: application/json" -d '{"limit": 10}' | jq .
```

If CIA repaired a malformed conversation:

```json
{
  "repairs": [
    {
      "seq": 0,
      "reason_code": "injected_synthetic_failure",
      "original_hash": "sha256:abc...",
      "repaired_hash": "sha256:def...",
      "severity": "warning"
    }
  ]
}
```

---

## The Claim (Defensible)

> *Substr8 is a governance plane for agent actions. If a framework can call tools, it can route those calls through Substr8 via MCP and get policy enforcement + tamper-evident audit + memory provenance.*

Validated with:
- LangGraph (state graph agents)
- PydanticAI (type-safe agents)
- AutoGen (multi-agent systems)

---

## Next Steps

- [CLI Documentation](../README.md)
- [FDAA Whitepaper](https://substr8labs.com/papers/fdaa)
- [Substr8 Website](https://substr8labs.com)

---

## License

MIT — Substr8 Labs 2026
