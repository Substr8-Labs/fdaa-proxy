"""
FDAA Proxy Server

FastAPI application for the FDAA Proxy gateway.
"""

import os
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import ProxyConfig, load_config
from .pool import GatewayPool
from .mcp import MCPPolicy
from .dct import DCTLogger
from .acc import ACCValidator

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fdaa-proxy")


# =============================================================================
# Pydantic Models
# =============================================================================

class GatewayRegisterRequest(BaseModel):
    """Request to register a gateway."""
    gateway_id: str
    server: str = Field(..., description="MCP server package, e.g. @anthropic/mcp-server-github")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables")
    policy: Dict[str, Any] = Field(default_factory=dict, description="Governance policy")


class ToolCallRequest(BaseModel):
    """Request to call a tool."""
    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    persona: Optional[str] = None
    role: Optional[str] = None
    reasoning: Optional[str] = Field(None, description="Agent's reasoning for this call")
    acc_token: Optional[str] = Field(None, description="ACC capability token")


class ApprovalRequest(BaseModel):
    """Request to approve/deny a pending call."""
    approved: bool
    approved_by: str


# =============================================================================
# Application Factory
# =============================================================================

def create_app(config: ProxyConfig = None) -> FastAPI:
    """Create FastAPI application."""
    
    # Initialize components
    dct_logger = None
    if config and config.dct.enabled:
        dct_logger = DCTLogger(
            storage=config.dct.storage,
            path=config.dct.path,
            mongodb_uri=config.dct.mongodb_uri,
            postgres_uri=config.dct.postgres_uri,
        )
    
    acc_validator = None
    if config and config.acc.enabled:
        acc_validator = ACCValidator(
            issuer=config.acc.issuer,
            public_key_path=config.acc.public_key_path,
            dev_mode=config.acc.dev_mode,
        )
    
    pool = GatewayPool(dct_logger=dct_logger)
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup and shutdown events."""
        logger.info("FDAA Proxy starting...")
        
        # Auto-connect configured gateways
        if config:
            for gw in config.gateways:
                if gw.auto_connect:
                    try:
                        # Build policy
                        policy_dict = {
                            "server": gw.server,
                            "mode": gw.policy.mode,
                            "write_requires_approval": gw.policy.write_requires_approval,
                            "delete_requires_approval": gw.policy.delete_requires_approval,
                            "admin_requires_approval": gw.policy.admin_requires_approval,
                            "tools": [
                                {
                                    "name": t.name,
                                    "category": t.category,
                                    "allowed": t.allowed,
                                    "requires_approval": t.requires_approval,
                                    "approvers": t.approvers,
                                }
                                for t in gw.policy.tools
                            ]
                        }
                        policy = MCPPolicy.from_dict(policy_dict)
                        
                        # Determine command
                        if gw.server.startswith("@"):
                            server_command = "npx"
                            server_args = ["-y", gw.server]
                        else:
                            parts = gw.server.split()
                            server_command = parts[0]
                            server_args = parts[1:] if len(parts) > 1 else []
                        
                        await pool.register(
                            gateway_id=gw.id,
                            server_command=server_command,
                            server_args=server_args,
                            server_env=gw.env,
                            policy=policy,
                        )
                        logger.info(f"Auto-connected gateway: {gw.id}")
                    except Exception as e:
                        logger.error(f"Failed to auto-connect {gw.id}: {e}")
        
        yield
        
        logger.info("FDAA Proxy shutting down...")
        await pool.disconnect_all()
    
    app = FastAPI(
        title="FDAA Proxy",
        description="Governed MCP Gateway with Cryptographic Audit Trails",
        version="0.1.0",
        lifespan=lifespan
    )
    
    # Store components in app state
    app.state.pool = pool
    app.state.dct_logger = dct_logger
    app.state.acc_validator = acc_validator
    app.state.config = config
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # =========================================================================
    # Routes
    # =========================================================================
    
    @app.get("/")
    async def root():
        """Health check and service info."""
        return {
            "service": "FDAA Proxy",
            "version": "0.1.0",
            "status": "running",
            "gateways": pool.gateway_count,
            "acc_enabled": acc_validator is not None,
            "dct_enabled": dct_logger is not None,
        }
    
    @app.get("/health")
    async def health():
        """Health check."""
        return {"status": "healthy"}
    
    # -------------------------------------------------------------------------
    # Gateway Management
    # -------------------------------------------------------------------------
    
    @app.get("/gateways")
    async def list_gateways():
        """List all connected gateways."""
        return {"gateways": pool.list()}
    
    @app.post("/gateways")
    async def register_gateway(request: GatewayRegisterRequest):
        """Register and connect a new MCP gateway."""
        # Build policy
        policy = MCPPolicy.from_dict({
            "server": request.server,
            **request.policy
        })
        
        # Determine command
        if request.server.startswith("@"):
            server_command = "npx"
            server_args = ["-y", request.server]
        else:
            parts = request.server.split()
            server_command = parts[0]
            server_args = parts[1:] if len(parts) > 1 else []
        
        try:
            result = await pool.register(
                gateway_id=request.gateway_id,
                server_command=server_command,
                server_args=server_args,
                server_env=request.env,
                policy=policy,
            )
            
            return {
                "status": "connected",
                "gateway_id": request.gateway_id,
                "server_info": result
            }
        except Exception as e:
            logger.error(f"Failed to connect gateway: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/gateways/{gateway_id}")
    async def disconnect_gateway(gateway_id: str):
        """Disconnect and remove a gateway."""
        if await pool.disconnect(gateway_id):
            return {"status": "disconnected", "gateway_id": gateway_id}
        raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
    
    @app.get("/gateways/{gateway_id}")
    async def get_gateway(gateway_id: str):
        """Get gateway status and stats."""
        gateway = pool.get(gateway_id)
        if not gateway:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
        
        return {
            "gateway_id": gateway_id,
            "connected": gateway.is_connected,
            "stats": gateway.get_stats()
        }
    
    # -------------------------------------------------------------------------
    # Tool Operations
    # -------------------------------------------------------------------------
    
    @app.get("/gateways/{gateway_id}/tools")
    async def list_tools(gateway_id: str, all: bool = False):
        """List available tools from a gateway."""
        gateway = pool.get(gateway_id)
        if not gateway:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
        
        if all:
            tools = gateway.list_all_tools()
        else:
            tools = gateway.list_tools()
        
        return {
            "gateway_id": gateway_id,
            "tools": [t.to_dict() for t in tools],
            "count": len(tools),
            "filtered": not all
        }
    
    @app.post("/gateways/{gateway_id}/call")
    async def call_tool(gateway_id: str, request: ToolCallRequest):
        """Call a tool through the gateway."""
        gateway = pool.get(gateway_id)
        if not gateway:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
        
        # Validate ACC token if provided and validator enabled
        if request.acc_token and acc_validator:
            validation = acc_validator.validate(request.acc_token)
            if not validation.valid:
                raise HTTPException(status_code=401, detail=f"ACC token invalid: {validation.error}")
        
        result = gateway.call_tool(
            tool_name=request.tool,
            arguments=request.arguments,
            persona=request.persona,
            role=request.role,
            reasoning=request.reasoning,
            acc_token=request.acc_token,
        )
        
        if not result.success:
            if "Approval required" in (result.error or ""):
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "pending_approval",
                        "message": result.error,
                        "tool": request.tool
                    }
                )
            
            if "Policy denied" in (result.error or ""):
                raise HTTPException(status_code=403, detail=result.error)
            
            raise HTTPException(status_code=500, detail=result.error)
        
        return {
            "status": "success",
            "tool": request.tool,
            "result": result.content
        }
    
    # -------------------------------------------------------------------------
    # Approval Workflow
    # -------------------------------------------------------------------------
    
    @app.get("/gateways/{gateway_id}/pending")
    async def list_pending_approvals(gateway_id: str):
        """List pending approval requests."""
        gateway = pool.get(gateway_id)
        if not gateway:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
        
        pending = gateway.list_pending_approvals()
        return {
            "gateway_id": gateway_id,
            "pending": [
                {
                    "id": p.id,
                    "tool": p.tool_name,
                    "arguments": p.arguments,
                    "created_at": p.created_at.isoformat(),
                    "approvers": p.approvers
                }
                for p in pending
            ]
        }
    
    @app.post("/gateways/{gateway_id}/approve/{request_id}")
    async def approve_request(gateway_id: str, request_id: str, request: ApprovalRequest):
        """Approve or deny a pending tool call."""
        gateway = pool.get(gateway_id)
        if not gateway:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway_id}' not found")
        
        result = gateway.approve_request(
            request_id=request_id,
            approved_by=request.approved_by,
            approved=request.approved
        )
        
        if not result.success:
            raise HTTPException(status_code=400, detail=result.error)
        
        return {
            "status": "approved" if request.approved else "denied",
            "request_id": request_id,
            "result": result.content if result.success else None
        }
    
    # -------------------------------------------------------------------------
    # Audit
    # -------------------------------------------------------------------------
    
    @app.get("/audit")
    async def query_audit(
        gateway_id: Optional[str] = None,
        event_type: Optional[str] = None,
        tool: Optional[str] = None,
        limit: int = 100
    ):
        """Query audit log."""
        if not dct_logger:
            raise HTTPException(status_code=503, detail="DCT logging not enabled")
        
        entries = dct_logger.query(
            gateway_id=gateway_id,
            event_type=event_type,
            tool=tool,
            limit=limit
        )
        
        return {
            "count": len(entries),
            "entries": [e.to_dict() for e in entries]
        }
    
    @app.get("/audit/verify")
    async def verify_audit():
        """Verify audit chain integrity."""
        if not dct_logger:
            raise HTTPException(status_code=503, detail="DCT logging not enabled")
        
        result = dct_logger.verify_chain()
        return {
            "valid": result.valid,
            "entries_checked": result.entries_checked,
            "first_invalid": result.first_invalid,
            "error": result.error,
        }
    
    @app.get("/audit/stats")
    async def audit_stats():
        """Get audit statistics."""
        if not dct_logger:
            raise HTTPException(status_code=503, detail="DCT logging not enabled")
        
        return dct_logger.stats
    
    return app


# =============================================================================
# Main
# =============================================================================

def main(config_path: str = None):
    """Run the FDAA Proxy server."""
    import uvicorn
    
    config = None
    if config_path:
        config = load_config(config_path)
    
    app = create_app(config)
    
    host = config.server.host if config else "0.0.0.0"
    port = config.server.port if config else 8766
    workers = config.server.workers if config else 1
    reload = config.server.reload if config else False
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        workers=workers,
        reload=reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()
