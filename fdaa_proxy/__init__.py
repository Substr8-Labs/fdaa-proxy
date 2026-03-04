"""
FDAA Proxy - Governed Gateway with Cryptographic Audit Trails

Three proxy modes:
1. Anthropic Proxy - HTTP proxy with CIA (Context Integrity Adapter) for LLM egress
2. OpenClaw Gateway Proxy - WebSocket proxy for OpenClaw with ACC/DCT
3. MCP Server Proxy - HTTP proxy for MCP servers (legacy)
"""

__version__ = "0.2.3"
__author__ = "Substr8 Labs"

from .config import ProxyConfig, load_config

# Lazy imports for modes that need optional dependencies
def create_app():
    """Import server app factory lazily (needs substr8)."""
    from .server import create_app as _create_app
    return _create_app()

__all__ = [
    "__version__",
    "ProxyConfig",
    "load_config", 
    "create_app",
]
