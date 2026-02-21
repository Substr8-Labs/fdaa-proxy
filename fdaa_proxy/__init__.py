"""
FDAA Proxy - Governed Gateway with Cryptographic Audit Trails

Two proxy modes:
1. OpenClaw Gateway Proxy - WebSocket proxy for OpenClaw with ACC/DCT
2. MCP Server Proxy - HTTP proxy for MCP servers (legacy)
"""

__version__ = "0.1.0"
__author__ = "Substr8 Labs"

from .config import ProxyConfig, load_config
from .server import create_app

__all__ = [
    "__version__",
    "ProxyConfig",
    "load_config", 
    "create_app",
]
