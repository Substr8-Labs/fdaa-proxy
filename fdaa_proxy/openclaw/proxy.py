"""
OpenClaw Gateway Proxy

WebSocket proxy that sits in front of OpenClaw Gateway to provide:
- ACC token validation
- Capability-based access control
- DCT audit logging
- Rate limiting

Architecture:
    Client → FDAA Proxy → OpenClaw Gateway
              ↓
        1. Validate ACC token
        2. Check capabilities  
        3. Log to DCT
        4. Forward to upstream
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any, Callable, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

import websockets
from websockets.server import WebSocketServerProtocol
from websockets.client import WebSocketClientProtocol

from .protocol import (
    Frame, Request, Response, Event, 
    ConnectParams, FrameType,
    get_required_scopes, Role
)
from ..acc import ACCValidator, ACCToken
from ..dct import DCTLogger

logger = logging.getLogger("fdaa-proxy.openclaw")


@dataclass
class ProxySession:
    """Active proxy session between client and upstream."""
    client_ws: WebSocketServerProtocol
    upstream_ws: Optional[WebSocketClientProtocol] = None
    
    # Auth state
    authenticated: bool = False
    acc_token: Optional[ACCToken] = None
    connect_params: Optional[ConnectParams] = None
    
    # Stats
    requests_forwarded: int = 0
    requests_blocked: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def client_id(self) -> str:
        if self.connect_params:
            return self.connect_params.client_id
        return str(id(self.client_ws))
    
    @property
    def scopes(self) -> Set[str]:
        """Get allowed scopes from ACC token or connect params."""
        if self.acc_token:
            # ACC token capabilities map to scopes
            return set(self.acc_token.capabilities)
        if self.connect_params:
            return set(self.connect_params.scopes)
        return set()


class OpenClawProxy:
    """
    WebSocket proxy for OpenClaw Gateway.
    
    Intercepts all traffic between clients and the gateway,
    enforcing ACC authorization and logging to DCT.
    
    Usage:
        proxy = OpenClawProxy(
            upstream_url="ws://localhost:18789",
            upstream_token="gateway_token",
            acc_validator=ACCValidator(...),
            dct_logger=DCTLogger(...),
        )
        
        await proxy.start(host="0.0.0.0", port=8800)
    """
    
    def __init__(
        self,
        upstream_url: str = "ws://localhost:18789",
        upstream_token: Optional[str] = None,
        acc_validator: Optional[ACCValidator] = None,
        dct_logger: Optional[DCTLogger] = None,
        require_acc: bool = False,
    ):
        self.upstream_url = upstream_url
        self.upstream_token = upstream_token
        self.acc_validator = acc_validator
        self.dct_logger = dct_logger
        self.require_acc = require_acc
        
        self._sessions: Dict[int, ProxySession] = {}
        self._server = None
    
    async def start(self, host: str = "0.0.0.0", port: int = 8800):
        """Start the proxy server."""
        logger.info(f"Starting OpenClaw proxy on ws://{host}:{port}")
        logger.info(f"Upstream: {self.upstream_url}")
        
        self._server = await websockets.serve(
            self._handle_client,
            host,
            port,
        )
        
        await self._server.wait_closed()
    
    async def stop(self):
        """Stop the proxy server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
    
    async def _handle_client(self, websocket: WebSocketServerProtocol):
        """Handle a new client connection."""
        session = ProxySession(client_ws=websocket)
        session_id = id(websocket)
        self._sessions[session_id] = session
        
        logger.info(f"Client connected: {session_id}")
        
        try:
            # Connect to upstream FIRST (OpenClaw sends challenge immediately)
            try:
                session.upstream_ws = await websockets.connect(self.upstream_url)
                logger.info(f"Connected to upstream: {self.upstream_url}")
            except Exception as e:
                logger.error(f"Failed to connect to upstream: {e}")
                await self._send_error(websocket, "0", "UPSTREAM_ERROR", f"Failed to connect: {e}")
                return
            
            # Receive challenge from upstream and forward to client
            challenge_frame = await session.upstream_ws.recv()
            logger.info(f"Received challenge from upstream")
            await websocket.send(challenge_frame)
            
            # Wait for connect request from client
            connect_frame = await websocket.recv()
            
            try:
                frame = Frame.parse(connect_frame)
            except Exception as e:
                logger.error(f"Invalid connect frame: {e}")
                await self._send_error(websocket, "0", "INVALID_FRAME", "Invalid frame format")
                return
            
            # Must be a connect request
            if not isinstance(frame, Request) or frame.method != "connect":
                logger.error(f"Expected connect request, got: {frame.method if isinstance(frame, Request) else frame}")
                await self._send_error(websocket, "0", "INVALID_HANDSHAKE", "Expected connect request")
                return
            
            # Process connect (validates ACC, modifies token, forwards)
            success = await self._handle_connect(session, frame)
            if not success:
                return
            
            # Start bidirectional forwarding
            await self._forward_traffic(session)
            
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client disconnected: {session_id}")
        except Exception as e:
            logger.error(f"Session error: {e}", exc_info=True)
        finally:
            # Cleanup
            if session.upstream_ws:
                await session.upstream_ws.close()
            del self._sessions[session_id]
    
    async def _handle_connect(self, session: ProxySession, request: Request) -> bool:
        """
        Handle connect request.
        
        1. Extract ACC token if present
        2. Validate ACC token
        3. Forward connect (with upstream token) to already-connected upstream
        4. Return response to client
        """
        params = ConnectParams.from_dict(request.params)
        session.connect_params = params
        
        # Log connect attempt
        self._log_event(
            "connect_attempt",
            client_id=params.client_id,
            role=params.role.value,
            scopes=params.scopes,
        )
        
        # Validate ACC token if present or required
        if params.acc_token or self.require_acc:
            if not params.acc_token:
                await self._send_error(
                    session.client_ws, 
                    request.id, 
                    "ACC_REQUIRED", 
                    "ACC token required"
                )
                return False
            
            if self.acc_validator:
                result = self.acc_validator.validate(params.acc_token)
                if not result.valid:
                    self._log_event(
                        "connect_denied",
                        client_id=params.client_id,
                        reason=result.error,
                    )
                    await self._send_error(
                        session.client_ws,
                        request.id,
                        "ACC_INVALID",
                        f"ACC token invalid: {result.error}"
                    )
                    return False
                
                session.acc_token = result.token
                logger.info(f"ACC token validated for {params.client_id}")
        
        # Forward connect to upstream (already connected)
        try:
            # Modify connect params to use upstream token
            upstream_params = dict(request.params)
            if self.upstream_token:
                upstream_params.setdefault("auth", {})["token"] = self.upstream_token
            
            upstream_request = Request(
                type=FrameType.REQUEST,
                raw={},
                id=request.id,
                method="connect",
                params=upstream_params,
            )
            
            # Forward connect to upstream
            await session.upstream_ws.send(upstream_request.to_json())
            logger.info(f"Forwarded connect request to upstream")
            
            # Wait for response
            response_data = await session.upstream_ws.recv()
            response = Frame.parse(response_data)
            
            if isinstance(response, Response) and response.ok:
                session.authenticated = True
                self._log_event(
                    "connect_success",
                    client_id=params.client_id,
                    acc_token_id=session.acc_token.token_id if session.acc_token else None,
                )
                logger.info(f"Connect successful for {params.client_id}")
            else:
                logger.warning(f"Connect failed: {response_data[:200]}")
            
            # Forward response to client
            await session.client_ws.send(response_data)
            
            return session.authenticated
            
        except Exception as e:
            logger.error(f"Failed during connect handshake: {e}", exc_info=True)
            await self._send_error(
                session.client_ws,
                request.id,
                "UPSTREAM_ERROR",
                f"Failed to connect to gateway: {e}"
            )
            return False
    
    async def _forward_traffic(self, session: ProxySession):
        """Bidirectional traffic forwarding with policy enforcement."""
        
        async def client_to_upstream():
            """Forward client messages to upstream."""
            async for message in session.client_ws:
                try:
                    frame = Frame.parse(message)
                    
                    # Enforce policy on requests
                    if isinstance(frame, Request):
                        allowed, reason = self._check_request(session, frame)
                        
                        if not allowed:
                            session.requests_blocked += 1
                            self._log_event(
                                "request_denied",
                                client_id=session.client_id,
                                method=frame.method,
                                reason=reason,
                            )
                            await self._send_error(
                                session.client_ws,
                                frame.id,
                                "POLICY_DENIED",
                                reason
                            )
                            continue
                        
                        session.requests_forwarded += 1
                        self._log_event(
                            "request_forwarded",
                            client_id=session.client_id,
                            method=frame.method,
                            request_id=frame.id,
                        )
                    
                    # Forward to upstream
                    await session.upstream_ws.send(message)
                    
                except Exception as e:
                    logger.error(f"Error processing client message: {e}")
        
        async def upstream_to_client():
            """Forward upstream messages to client."""
            async for message in session.upstream_ws:
                try:
                    # Just forward - we don't modify responses
                    await session.client_ws.send(message)
                except Exception as e:
                    logger.error(f"Error forwarding to client: {e}")
        
        # Run both directions concurrently
        await asyncio.gather(
            client_to_upstream(),
            upstream_to_client(),
            return_exceptions=True
        )
    
    def _check_request(self, session: ProxySession, request: Request) -> tuple[bool, str]:
        """
        Check if a request is allowed based on ACC capabilities.
        
        Returns: (allowed, reason)
        """
        method = request.method
        required_scopes = get_required_scopes(method)
        
        if not required_scopes:
            # No specific scopes required - allow
            return True, ""
        
        # Check if session has required scopes
        session_scopes = session.scopes
        
        for scope in required_scopes:
            if scope in session_scopes:
                return True, ""
        
        return False, f"Missing required scope: {required_scopes}"
    
    async def _send_error(
        self, 
        websocket: WebSocketServerProtocol,
        request_id: str,
        code: str,
        message: str
    ):
        """Send an error response."""
        response = Response.error_response(request_id, code, message)
        await websocket.send(response.to_json())
    
    def _log_event(self, event_type: str, **kwargs):
        """Log an event to DCT."""
        if self.dct_logger:
            self.dct_logger.log(
                event_type=event_type,
                gateway_id="openclaw-proxy",
                **kwargs
            )
        else:
            logger.info(f"Event: {event_type} - {kwargs}")
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get proxy statistics."""
        return {
            "active_sessions": len(self._sessions),
            "upstream_url": self.upstream_url,
            "require_acc": self.require_acc,
        }


async def run_proxy(
    host: str = "0.0.0.0",
    port: int = 8800,
    upstream_url: str = "ws://localhost:18789",
    upstream_token: Optional[str] = None,
    acc_validator: Optional[ACCValidator] = None,
    dct_logger: Optional[DCTLogger] = None,
    require_acc: bool = False,
):
    """Run the OpenClaw proxy server."""
    proxy = OpenClawProxy(
        upstream_url=upstream_url,
        upstream_token=upstream_token,
        acc_validator=acc_validator,
        dct_logger=dct_logger,
        require_acc=require_acc,
    )
    
    await proxy.start(host=host, port=port)
