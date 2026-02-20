"""
FDAA Proxy - Governed MCP Gateway with Cryptographic Audit Trails

The FDAA Proxy sits between AI agents and MCP servers, enforcing governance
policies and creating verifiable audit trails.
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
