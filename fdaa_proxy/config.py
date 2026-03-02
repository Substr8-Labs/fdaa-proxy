"""
Configuration management for FDAA Proxy.

Supports YAML configuration with environment variable expansion.
"""

import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

import yaml


@dataclass
class ServerConfig:
    """HTTP server configuration."""
    host: str = "0.0.0.0"
    port: int = 8766
    workers: int = 1
    reload: bool = False


@dataclass
class ACCConfig:
    """ACC (Agent Capability Certificate) validation configuration."""
    enabled: bool = False
    issuer: Optional[str] = None
    public_key_path: Optional[str] = None
    # For development: skip validation
    dev_mode: bool = False


@dataclass
class DCTConfig:
    """DCT (Deterministic Computation Trail) audit configuration."""
    enabled: bool = True
    storage: str = "sqlite"  # sqlite | mongodb | postgres | memory
    path: str = "./audit.db"
    mongodb_uri: Optional[str] = None
    postgres_uri: Optional[str] = None


@dataclass
class AgentRegistryConfig:
    """Agent Registry configuration."""
    enabled: bool = True
    db_path: str = "./data/agents.db"
    openclaw_url: str = "http://localhost:18789"
    openclaw_password: Optional[str] = None


@dataclass  
class ToolPolicyConfig:
    """Policy for a single tool."""
    name: str
    category: str = "read"  # read | write | delete | admin
    allowed: bool = True
    requires_approval: bool = False
    approvers: List[str] = field(default_factory=list)
    personas: List[str] = field(default_factory=lambda: ["*"])
    roles: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class GatewayPolicyConfig:
    """Policy configuration for a gateway."""
    mode: str = "allowlist"  # allowlist | blocklist
    tools: List[ToolPolicyConfig] = field(default_factory=list)
    write_requires_approval: bool = True
    delete_requires_approval: bool = True
    admin_requires_approval: bool = True


@dataclass
class GatewayConfig:
    """Configuration for a single MCP gateway."""
    id: str
    server: str  # MCP server package or command
    env: Dict[str, str] = field(default_factory=dict)
    policy: GatewayPolicyConfig = field(default_factory=GatewayPolicyConfig)
    auto_connect: bool = True


@dataclass
class ProxyConfig:
    """Root configuration for FDAA Proxy."""
    server: ServerConfig = field(default_factory=ServerConfig)
    acc: ACCConfig = field(default_factory=ACCConfig)
    dct: DCTConfig = field(default_factory=DCTConfig)
    agents: AgentRegistryConfig = field(default_factory=AgentRegistryConfig)
    gateways: List[GatewayConfig] = field(default_factory=list)


def expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(value, str):
        # Match ${VAR} or $VAR patterns
        pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'
        
        def replace(match):
            var_name = match.group(1) or match.group(2)
            return os.environ.get(var_name, match.group(0))
        
        return re.sub(pattern, replace, value)
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    return value


def parse_gateway_config(gateway_id: str, data: Dict[str, Any]) -> GatewayConfig:
    """Parse a gateway configuration from dict."""
    policy_data = data.get("policy", {})
    
    tools = []
    for tool in policy_data.get("tools", []):
        if isinstance(tool, str):
            tools.append(ToolPolicyConfig(name=tool))
        else:
            tools.append(ToolPolicyConfig(
                name=tool.get("name"),
                category=tool.get("category", "read"),
                allowed=tool.get("allowed", True),
                requires_approval=tool.get("requires_approval", False),
                approvers=tool.get("approvers", []),
                personas=tool.get("personas", ["*"]),
                roles=tool.get("roles", ["*"]),
            ))
    
    policy = GatewayPolicyConfig(
        mode=policy_data.get("mode", "allowlist"),
        tools=tools,
        write_requires_approval=policy_data.get("write_requires_approval", True),
        delete_requires_approval=policy_data.get("delete_requires_approval", True),
        admin_requires_approval=policy_data.get("admin_requires_approval", True),
    )
    
    return GatewayConfig(
        id=gateway_id,
        server=data.get("server", ""),
        env=data.get("env", {}),
        policy=policy,
        auto_connect=data.get("auto_connect", True),
    )


def load_config(path: str | Path) -> ProxyConfig:
    """Load configuration from YAML file."""
    path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    
    with open(path) as f:
        raw = yaml.safe_load(f)
    
    # Expand environment variables
    data = expand_env_vars(raw)
    
    # Parse server config
    server_data = data.get("server", {})
    server = ServerConfig(
        host=server_data.get("host", "0.0.0.0"),
        port=server_data.get("port", 8766),
        workers=server_data.get("workers", 1),
        reload=server_data.get("reload", False),
    )
    
    # Parse ACC config
    acc_data = data.get("acc", {})
    acc = ACCConfig(
        enabled=acc_data.get("enabled", False),
        issuer=acc_data.get("issuer"),
        public_key_path=acc_data.get("public_key_path"),
        dev_mode=acc_data.get("dev_mode", False),
    )
    
    # Parse DCT config
    dct_data = data.get("dct", {})
    dct = DCTConfig(
        enabled=dct_data.get("enabled", True),
        storage=dct_data.get("storage", "sqlite"),
        path=dct_data.get("path", "./audit.db"),
        mongodb_uri=dct_data.get("mongodb_uri"),
        postgres_uri=dct_data.get("postgres_uri"),
    )
    
    # Parse gateways
    gateways = []
    for gateway_id, gateway_data in data.get("gateways", {}).items():
        gateways.append(parse_gateway_config(gateway_id, gateway_data))
    
    return ProxyConfig(
        server=server,
        acc=acc,
        dct=dct,
        gateways=gateways,
    )


def create_default_config() -> str:
    """Generate default configuration YAML."""
    return """# FDAA Proxy Configuration
# https://github.com/Substr8-Labs/fdaa-proxy

server:
  host: 0.0.0.0
  port: 8766
  workers: 1

# ACC capability token validation
acc:
  enabled: false
  # issuer: "https://acc.substr8labs.com"
  # public_key_path: /etc/fdaa/acc-public.pem
  dev_mode: true  # Skip validation in development

# DCT audit chain
dct:
  enabled: true
  storage: sqlite
  path: ./audit.db

# MCP server connections
gateways:
  # Example: GitHub gateway
  # github:
  #   server: "@anthropic/mcp-server-github"
  #   env:
  #     GITHUB_TOKEN: ${GITHUB_TOKEN}
  #   policy:
  #     mode: allowlist
  #     tools:
  #       - name: get_file_contents
  #         category: read
  #       - name: create_issue
  #         category: write
  #         requires_approval: true
"""
