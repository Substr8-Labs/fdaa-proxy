"""
Anthropic API HTTP Proxy

HTTP reverse proxy that sits in front of Anthropic API to provide:
- CIA (Context Integrity Adapter) validation/repair of messages[]
- DCT audit logging
- Tool pairing corruption prevention

Architecture:
    OpenClaw → FDAA Proxy → api.anthropic.com
                  ↓
            1. Validate/repair messages[]
            2. Log to DCT
            3. Forward to Anthropic
            4. Return response

This is Topology A - the proxy IS the Anthropic egress point.
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib

import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse, JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from pathlib import Path

from .ril import ContextIntegrityAdapter, RepairMode
from .dct import DCTLogger

logger = logging.getLogger("fdaa-proxy.anthropic")


@dataclass
class ProxyStats:
    """Statistics for the proxy."""
    requests_total: int = 0
    requests_repaired: int = 0
    requests_failed: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AnthropicProxy:
    """
    HTTP reverse proxy for Anthropic API.
    
    Intercepts all /v1/messages requests, validates/repairs the messages[]
    payload using CIA, then forwards to the real Anthropic API.
    
    Usage:
        proxy = AnthropicProxy(
            anthropic_api_key="sk-ant-...",
            cia_mode="permissive",
            dct_logger=DCTLogger(...),
        )
        
        app = proxy.create_app()
        uvicorn.run(app, host="0.0.0.0", port=18800)
    """
    
    # Anthropic API base URL
    ANTHROPIC_BASE_URL = "https://api.anthropic.com"
    
    def __init__(
        self,
        anthropic_api_key: str,
        cia_mode: str = "permissive",
        dct_logger: Optional[DCTLogger] = None,
        audit_db_path: str = "./data/anthropic-audit.db",
    ):
        self.anthropic_api_key = anthropic_api_key
        self.audit_db_path = audit_db_path
        
        # Auto-create DCTLogger if path provided and no logger passed
        if dct_logger is not None:
            self.dct_logger = dct_logger
        elif audit_db_path:
            # Ensure parent directory exists
            db_path = Path(audit_db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.dct_logger = DCTLogger(storage="sqlite", path=str(db_path))
            logger.info(f"DCTLogger initialized: {audit_db_path}")
        else:
            self.dct_logger = None
        
        # CIA for message validation/repair
        self.cia = ContextIntegrityAdapter(mode=RepairMode(cia_mode))
        
        # Stats
        self.stats = ProxyStats()
        
        # HTTP client for upstream
        self._client: Optional[httpx.AsyncClient] = None
        
        logger.info(f"AnthropicProxy initialized: CIA={cia_mode}, DCT={'enabled' if self.dct_logger else 'disabled'}")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.ANTHROPIC_BASE_URL,
                timeout=httpx.Timeout(300.0),  # 5 minute timeout for long responses
            )
        return self._client
    
    async def _close_client(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _compute_request_hash(self, body: bytes) -> str:
        """Compute hash of request body for audit."""
        return hashlib.sha256(body).hexdigest()[:16]
    
    async def _handle_messages(self, request: Request) -> Response:
        """Handle POST /v1/messages - the main Anthropic API endpoint."""
        request_id = f"req_{int(time.time() * 1000)}"
        start_time = time.time()
        
        self.stats.requests_total += 1
        
        try:
            # Read request body
            body = await request.body()
            self.stats.bytes_in += len(body)
            
            # Parse JSON
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"[{request_id}] Invalid JSON: {e}")
                return JSONResponse(
                    {"error": {"type": "invalid_request_error", "message": str(e)}},
                    status_code=400
                )
            
            # Extract messages for CIA validation
            messages = payload.get("messages", [])
            original_count = len(messages)
            
            # Run CIA validation/repair (fail-open)
            try:
                repaired_messages, repair_report = self.cia.process(messages)
            except Exception as e:
                logger.exception(f"[{request_id}] CIA failed (fail-open), forwarding original messages: {e}")
                repaired_messages, repair_report = messages, {"fail_open": True, "error": str(e)}

            if repair_report:
                self.stats.requests_repaired += 1
                logger.warning(f"[{request_id}] CIA repaired messages: {repair_report}")
                
                # Log repair to DCT if available
                if self.dct_logger:
                    self._log_repair(request_id, repair_report, payload.get("model"))
            
            # Update payload with repaired messages
            payload["messages"] = repaired_messages
            repaired_body = json.dumps(payload).encode()
            
            # Forward to Anthropic
            client = await self._get_client()
            
            # Build headers (forward most, add our auth)
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.anthropic_api_key,
                "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            }
            
            # Check if streaming
            is_streaming = payload.get("stream", False)
            
            if is_streaming:
                return await self._handle_streaming(
                    client, repaired_body, headers, request_id, start_time
                )
            else:
                return await self._handle_blocking(
                    client, repaired_body, headers, request_id, start_time
                )
                
        except Exception as e:
            self.stats.requests_failed += 1
            logger.exception(f"[{request_id}] Proxy error: {e}")
            return JSONResponse(
                {"error": {"type": "api_error", "message": f"Proxy error: {e}"}},
                status_code=500
            )
    
    async def _handle_blocking(
        self, 
        client: httpx.AsyncClient, 
        body: bytes, 
        headers: dict,
        request_id: str,
        start_time: float
    ) -> Response:
        """Handle non-streaming request."""
        response = await client.post("/v1/messages", content=body, headers=headers)
        
        elapsed = time.time() - start_time
        self.stats.bytes_out += len(response.content)
        
        logger.info(f"[{request_id}] Completed in {elapsed:.2f}s, status={response.status_code}")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    
    async def _handle_streaming(
        self,
        client: httpx.AsyncClient,
        body: bytes,
        headers: dict,
        request_id: str,
        start_time: float
    ) -> StreamingResponse:
        """Handle streaming request with Server-Sent Events."""
        
        async def stream_response():
            bytes_out = 0
            try:
                async with client.stream(
                    "POST", "/v1/messages", content=body, headers=headers
                ) as response:
                    if response.status_code != 200:
                        # Forward error
                        error_body = await response.aread()
                        yield error_body
                        return
                    
                    async for chunk in response.aiter_bytes():
                        bytes_out += len(chunk)
                        yield chunk
                        
            except Exception as e:
                logger.error(f"[{request_id}] Streaming error: {e}")
                yield f'data: {{"type":"error","error":{{"type":"api_error","message":"{e}"}}}}\n\n'.encode()
            finally:
                elapsed = time.time() - start_time
                self.stats.bytes_out += bytes_out
                logger.info(f"[{request_id}] Stream completed in {elapsed:.2f}s, {bytes_out} bytes")
        
        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    
    def _log_repair(self, request_id: str, report, model: str):
        """Log CIA repair to DCT audit trail."""
        if not self.dct_logger:
            return
        
        try:
            # Convert RepairResult to dict if needed
            result_dict = report.to_dict() if hasattr(report, 'to_dict') else report
            
            # DCTLogger.log() is synchronous
            self.dct_logger.log(
                event_type="cia_repair",
                gateway_id="anthropic-proxy",
                tool=None,
                arguments={"request_id": request_id, "model": model},
                result=result_dict,
            )
        except Exception as e:
            logger.warning(f"Failed to log repair to DCT: {e}")
    
    async def _handle_health(self, request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({
            "status": "ok",
            "service": "fdaa-anthropic-proxy",
            "cia_mode": self.cia.mode.value,
            "stats": {
                "requests_total": self.stats.requests_total,
                "requests_repaired": self.stats.requests_repaired,
                "requests_failed": self.stats.requests_failed,
                "bytes_in": self.stats.bytes_in,
                "bytes_out": self.stats.bytes_out,
                "uptime_seconds": (datetime.now(timezone.utc) - self.stats.started_at).total_seconds(),
            },
            "cia_stats": self.cia.get_stats(),
        })
    
    async def _handle_status(self, request: Request) -> JSONResponse:
        """Detailed status endpoint."""
        return JSONResponse({
            "proxy": "fdaa-anthropic-proxy",
            "upstream": self.ANTHROPIC_BASE_URL,
            "cia": {
                "enabled": True,
                "mode": self.cia.mode.value,
                "stats": self.cia.get_stats(),
            },
            "stats": {
                "requests_total": self.stats.requests_total,
                "requests_repaired": self.stats.requests_repaired,
                "requests_failed": self.stats.requests_failed,
                "repair_rate": (
                    self.stats.requests_repaired / self.stats.requests_total 
                    if self.stats.requests_total > 0 else 0
                ),
                "uptime_seconds": (datetime.now(timezone.utc) - self.stats.started_at).total_seconds(),
            },
        })
    
    def create_app(self) -> Starlette:
        """Create Starlette application."""
        routes = [
            # Anthropic API endpoints
            Route("/v1/messages", self._handle_messages, methods=["POST"]),
            
            # Health/status
            Route("/health", self._handle_health, methods=["GET"]),
            Route("/status", self._handle_status, methods=["GET"]),
            Route("/", self._handle_health, methods=["GET"]),
        ]
        
        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
        ]
        
        app = Starlette(
            routes=routes,
            middleware=middleware,
            on_startup=[self._on_startup],
            on_shutdown=[self._on_shutdown],
        )
        
        return app
    
    async def _on_startup(self):
        """Startup hook."""
        logger.info("AnthropicProxy starting up...")
        await self._get_client()  # Pre-warm client
    
    async def _on_shutdown(self):
        """Shutdown hook."""
        logger.info("AnthropicProxy shutting down...")
        await self._close_client()


def create_anthropic_proxy(
    api_key: str,
    cia_mode: str = "permissive",
    audit_db_path: str = "./data/anthropic-audit.db",
) -> Starlette:
    """
    Factory function to create the Anthropic proxy app.
    
    Args:
        api_key: Anthropic API key
        cia_mode: CIA mode (strict, permissive, forensic)
        audit_db_path: Path for audit database
        
    Returns:
        Starlette application
    """
    proxy = AnthropicProxy(
        anthropic_api_key=api_key,
        cia_mode=cia_mode,
        audit_db_path=audit_db_path,
    )
    return proxy.create_app()
