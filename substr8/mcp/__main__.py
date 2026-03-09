"""
Entry point for running MCP server directly.

Usage:
    python -m substr8.mcp
    python -m substr8.mcp --host 0.0.0.0 --port 3456
"""

import argparse
import os
from .server import create_server


def main():
    parser = argparse.ArgumentParser(description="Substr8 MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=3456, help="Port to listen on")
    parser.add_argument("--cia-db", help="Path to CIA audit database")
    parser.add_argument("--cia-url", default="http://localhost:18800/status", 
                       help="CIA status endpoint URL")
    parser.add_argument("--require-auth", action="store_true", 
                       help="Require API key for all requests")
    parser.add_argument("--api-keys-file", help="Path to API keys JSON file")
    parser.add_argument("--no-rate-limit", action="store_true", 
                       help="Disable rate limiting")
    parser.add_argument("--local", action="store_true", 
                       help="Run fully local (no hosted control plane)")
    
    args = parser.parse_args()
    
    # Check environment for overrides
    host = os.environ.get("MCP_HOST", args.host)
    port = int(os.environ.get("MCP_PORT", args.port))
    cia_db = os.environ.get("CIA_AUDIT_DB", args.cia_db)
    require_auth = os.environ.get("MCP_REQUIRE_AUTH", "").lower() == "true" or args.require_auth
    
    print(f"Starting Substr8 MCP Server")
    print(f"  Host: {host}")
    print(f"  Port: {port}")
    print(f"  Auth: {'required' if require_auth else 'optional'}")
    print(f"  CIA DB: {cia_db or 'not configured'}")
    print()
    
    server = create_server(
        host=host,
        port=port,
        local_mode=args.local,
        cia_audit_db=cia_db,
        cia_status_url=args.cia_url,
        require_auth=require_auth,
        api_keys_file=args.api_keys_file,
        rate_limiting=not args.no_rate_limit
    )
    
    server.run()


if __name__ == "__main__":
    main()
