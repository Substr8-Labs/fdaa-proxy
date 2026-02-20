"""
Gateway Pool Manager

Manages persistent connections to multiple MCP servers.
Each gateway is kept alive and reused across requests.
"""

import asyncio
import logging
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime, timezone

from ..mcp import MCPGateway, MCPPolicy, AuditEntry
from ..dct import DCTLogger

logger = logging.getLogger("fdaa-proxy.pool")


class GatewayPool:
    """
    Manages persistent connections to MCP servers.
    
    Features:
    - Connection pooling
    - Automatic reconnection
    - Unified audit logging via DCT
    - Gateway lifecycle management
    """
    
    def __init__(self, dct_logger: DCTLogger = None):
        self._gateways: Dict[str, MCPGateway] = {}
        self._configs: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._dct_logger = dct_logger
    
    async def register(
        self,
        gateway_id: str,
        server_command: str,
        server_args: List[str],
        server_env: Dict[str, str],
        policy: MCPPolicy,
    ) -> Dict[str, Any]:
        """Register and connect a gateway."""
        async with self._lock:
            # Disconnect existing if any
            if gateway_id in self._gateways:
                try:
                    self._gateways[gateway_id].disconnect()
                except Exception:
                    pass
            
            # Create audit callback
            def audit_callback(entry: AuditEntry):
                if self._dct_logger:
                    self._dct_logger.log(
                        event_type="tool_call",
                        gateway_id=gateway_id,
                        tool=entry.tool,
                        arguments=entry.arguments,
                        result=entry.result,
                        error=entry.error,
                        persona=entry.persona,
                        role=entry.role,
                        reasoning=entry.reasoning,
                        acc_token_id=entry.acc_token_id,
                    )
            
            # Create new gateway
            gateway = MCPGateway(
                server_command=server_command,
                server_args=server_args,
                server_env=server_env,
                policy=policy,
                audit_callback=audit_callback if self._dct_logger else None,
            )
            
            # Connect
            result = gateway.connect()
            
            self._gateways[gateway_id] = gateway
            self._configs[gateway_id] = {
                "server_command": server_command,
                "server_args": server_args,
                "policy": policy.to_dict(),
                "connected_at": datetime.now(timezone.utc).isoformat()
            }
            
            logger.info(f"Gateway '{gateway_id}' connected: {result}")
            
            # Log to DCT
            if self._dct_logger:
                self._dct_logger.log(
                    event_type="gateway_connect",
                    gateway_id=gateway_id,
                    result=result,
                )
            
            return result
    
    async def disconnect(self, gateway_id: str) -> bool:
        """Disconnect a gateway."""
        async with self._lock:
            if gateway_id not in self._gateways:
                return False
            
            try:
                self._gateways[gateway_id].disconnect()
            except Exception:
                pass
            
            del self._gateways[gateway_id]
            if gateway_id in self._configs:
                del self._configs[gateway_id]
            
            logger.info(f"Gateway '{gateway_id}' disconnected")
            
            # Log to DCT
            if self._dct_logger:
                self._dct_logger.log(
                    event_type="gateway_disconnect",
                    gateway_id=gateway_id,
                )
            
            return True
    
    def get(self, gateway_id: str) -> Optional[MCPGateway]:
        """Get a connected gateway."""
        return self._gateways.get(gateway_id)
    
    def list(self) -> List[Dict[str, Any]]:
        """List all connected gateways."""
        result = []
        for gid, gateway in self._gateways.items():
            config = self._configs.get(gid, {})
            result.append({
                "gateway_id": gid,
                "connected": gateway.is_connected,
                "stats": gateway.get_stats(),
                "connected_at": config.get("connected_at")
            })
        return result
    
    async def disconnect_all(self):
        """Disconnect all gateways (for shutdown)."""
        async with self._lock:
            for gid, gateway in list(self._gateways.items()):
                try:
                    gateway.disconnect()
                except Exception:
                    pass
            self._gateways.clear()
            self._configs.clear()
    
    @property
    def gateway_count(self) -> int:
        """Number of connected gateways."""
        return len(self._gateways)
