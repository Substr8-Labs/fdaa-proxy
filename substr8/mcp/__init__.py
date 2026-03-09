"""
Substr8 MCP Server - Universal Framework Bridge

Exposes Substr8 governance tools (ACC, DCT, GAM) via the Model Context Protocol,
allowing any compatible agent framework to connect.

Usage:
    # CLI
    substr8 mcp start
    substr8 mcp status
    substr8 mcp tools
    
    # Python
    from substr8.mcp import create_server
    server = create_server(port=3456)
    server.run()
"""

from .server import (
    Substr8MCPServer,
    MCPServerConfig,
    Run,
    create_server,
)

__all__ = [
    "Substr8MCPServer",
    "MCPServerConfig", 
    "Run",
    "create_server",
]
