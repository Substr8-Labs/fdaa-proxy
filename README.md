# FDAA Proxy

**Governed Gateway with Cryptographic Audit Trails**

The FDAA Proxy provides governance for AI agent runtimes:

1. **OpenClaw Gateway Proxy** — WebSocket proxy for OpenClaw with ACC/DCT
2. **MCP Server Proxy** — HTTP proxy for MCP servers (legacy)

```
Agent → FDAA Proxy → Policy Check → Audit Log → Upstream MCP Server
                  ↓
            ACC Token Validation
                  ↓  
            DCT Audit Chain
```

## Features

- **W^X Enforcement** — Separate read and write permissions (Write XOR Execute)
- **Policy Engine** — Allowlist/blocklist tools, rate limiting, approval workflows
- **ACC Integration** — Validate capability tokens before allowing operations
- **DCT Audit** — Cryptographic audit trail with hash chain verification
- **Virtual MCP Server** — Expose filtered tool surface to agents
- **Approval Workflows** — Human-in-the-loop for high-risk operations

## Quick Start

### OpenClaw Gateway Proxy (Primary)

```bash
# Install
pip install fdaa-proxy

# Start the proxy in front of OpenClaw Gateway
fdaa-proxy openclaw start --upstream ws://localhost:18789

# With ACC token requirement
fdaa-proxy openclaw start --require-acc --upstream ws://localhost:18789
```

### MCP Server Proxy (Legacy)

```bash
# Configure
fdaa-proxy init

# Start MCP gateway
fdaa-proxy start --config gateway.yaml
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        FDAA Proxy                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ ACC Layer   │  │ Policy      │  │ DCT Audit           │  │
│  │             │  │ Engine      │  │ Logger              │  │
│  │ - Token     │  │             │  │                     │  │
│  │   validation│  │ - W^X rules │  │ - Hash chains       │  │
│  │ - Capability│  │ - Allowlist │  │ - Tamper detection  │  │
│  │   checking  │  │ - Rate limit│  │ - Event logging     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    Gateway Pool                         ││
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              ││
│  │  │ GitHub   │  │ Slack    │  │ Custom   │  ...         ││
│  │  │ Gateway  │  │ Gateway  │  │ Gateway  │              ││
│  │  └──────────┘  └──────────┘  └──────────┘              ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## Configuration

```yaml
# gateway.yaml
server:
  host: 0.0.0.0
  port: 8766

# ACC capability token validation
acc:
  enabled: true
  issuer: "https://acc.substr8labs.com"
  # Or local validation
  # public_key_path: /etc/fdaa/acc-public.pem

# DCT audit chain
dct:
  enabled: true
  storage: sqlite  # sqlite | mongodb | postgres
  path: /var/lib/fdaa/audit.db

# MCP server connections
gateways:
  github:
    server: "@anthropic/mcp-server-github"
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    policy:
      mode: allowlist
      tools:
        - name: get_file_contents
          category: read
        - name: create_issue
          category: write
          requires_approval: true

  slack:
    server: "@anthropic/mcp-server-slack"
    env:
      SLACK_TOKEN: ${SLACK_TOKEN}
    policy:
      mode: allowlist
      tools:
        - name: list_channels
          category: read
```

## CLI Commands

```bash
# Gateway management
fdaa-proxy start              # Start gateway server
fdaa-proxy stop               # Stop gateway server  
fdaa-proxy status             # Show gateway status
fdaa-proxy reload             # Reload configuration

# Gateway operations
fdaa-proxy gateways list      # List connected gateways
fdaa-proxy gateways connect   # Connect a new gateway
fdaa-proxy gateways tools     # List available tools

# Audit
fdaa-proxy audit list         # Query audit log
fdaa-proxy audit verify       # Verify hash chain integrity
fdaa-proxy audit export       # Export audit log

# Approvals
fdaa-proxy approvals list     # List pending approvals
fdaa-proxy approvals approve  # Approve a request
fdaa-proxy approvals deny     # Deny a request
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/gateways` | GET | List connected gateways |
| `/gateways` | POST | Register new gateway |
| `/gateways/{id}` | DELETE | Disconnect gateway |
| `/gateways/{id}/tools` | GET | List available tools |
| `/gateways/{id}/call` | POST | Call a tool |
| `/gateways/{id}/pending` | GET | List pending approvals |
| `/gateways/{id}/approve/{req}` | POST | Approve/deny request |
| `/audit` | GET | Query audit log |

## Integration with Substr8

FDAA Proxy is part of the Substr8 platform stack:

- **FDAA** — Deterministic execution foundation
- **ACC** — Capability token system (this validates tokens)
- **DCT** — Cryptographic audit trails (this logs here)
- **GAM** — Agent memory system

```bash
# Via substr8 CLI
substr8 gateway start
substr8 gateway status
substr8 audit verify
```

## License

Apache-2.0

## Links

- [FDAA Whitepaper](https://github.com/Substr8-Labs/whitepapers/tree/main/fdaa)
- [ACC Whitepaper](https://github.com/Substr8-Labs/whitepapers/tree/main/acc)
- [DCT Whitepaper](https://github.com/Substr8-Labs/whitepapers/tree/main/dct)
