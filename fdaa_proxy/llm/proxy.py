"""
LLM HTTP Reverse Proxy

Sits in front of Anthropic-compatible APIs (like OpenClaw Bridge)
to provide governance, audit, and access control.
"""

import os
import json
import time
import hashlib
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

logger = logging.getLogger("fdaa-proxy.llm")


class LLMProxy:
    """
    HTTP reverse proxy for LLM APIs.
    
    Routes:
        POST /v1/messages -> upstream (Anthropic-compatible)
        GET /health -> health check
    """
    
    def __init__(
        self,
        upstream_url: str,
        upstream_auth: Optional[str] = None,
        acc_enabled: bool = False,
        acc_public_key: Optional[str] = None,
        dct_enabled: bool = True,
        dct_path: str = "/data/audit/dct.jsonl",
    ):
        self.upstream_url = upstream_url.rstrip("/")
        self.upstream_auth = upstream_auth
        self.acc_enabled = acc_enabled
        self.acc_public_key = acc_public_key
        self.dct_enabled = dct_enabled
        self.dct_path = dct_path
        
        self.app = FastAPI(title="FDAA LLM Proxy")
        self._setup_routes()
        self._setup_middleware()
        
        # Stats
        self.requests_total = 0
        self.requests_blocked = 0
        
    def _setup_middleware(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
    def _setup_routes(self):
        @self.app.get("/health")
        async def health():
            return {
                "status": "ok",
                "service": "fdaa-llm-proxy",
                "upstream": self.upstream_url,
                "acc_enabled": self.acc_enabled,
                "dct_enabled": self.dct_enabled,
                "stats": {
                    "requests_total": self.requests_total,
                    "requests_blocked": self.requests_blocked,
                }
            }
        
        @self.app.post("/v1/messages")
        async def proxy_messages(
            request: Request,
            authorization: Optional[str] = Header(None),
            x_acc_token: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None),
        ):
            """Proxy Anthropic-compatible /v1/messages requests."""
            
            self.requests_total += 1
            start_time = time.time()
            request_id = x_request_id or hashlib.sha256(
                f"{time.time()}{self.requests_total}".encode()
            ).hexdigest()[:16]
            
            # Get request body
            try:
                body = await request.json()
            except Exception as e:
                raise HTTPException(400, f"Invalid JSON: {e}")
            
            # ACC validation (if enabled)
            if self.acc_enabled:
                if not x_acc_token:
                    self.requests_blocked += 1
                    self._log_dct({
                        "event": "blocked",
                        "reason": "missing_acc_token",
                        "request_id": request_id,
                    })
                    raise HTTPException(401, "ACC token required")
                
                # TODO: Validate ACC token signature
                # For now, just check it exists
                logger.info(f"ACC token present: {x_acc_token[:20]}...")
            
            # Log request to DCT
            if self.dct_enabled:
                self._log_dct({
                    "event": "request",
                    "request_id": request_id,
                    "model": body.get("model"),
                    "messages_count": len(body.get("messages", [])),
                    "max_tokens": body.get("max_tokens"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            
            # Forward to upstream
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    headers = {
                        "Content-Type": "application/json",
                    }
                    
                    # Pass through upstream auth
                    if self.upstream_auth:
                        headers["Authorization"] = f"Bearer {self.upstream_auth}"
                    
                    response = await client.post(
                        f"{self.upstream_url}/v1/messages",
                        json=body,
                        headers=headers,
                    )
                    
                    result = response.json()
                    latency_ms = int((time.time() - start_time) * 1000)
                    
                    # Log response to DCT
                    if self.dct_enabled:
                        self._log_dct({
                            "event": "response",
                            "request_id": request_id,
                            "status": response.status_code,
                            "model": result.get("model"),
                            "usage": result.get("usage"),
                            "latency_ms": latency_ms,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    
                    # Add proxy metadata
                    result["_fdaa"] = {
                        "request_id": request_id,
                        "latency_ms": latency_ms,
                        "proxy": "fdaa-llm-proxy",
                    }
                    
                    return JSONResponse(
                        content=result,
                        status_code=response.status_code,
                    )
                    
            except httpx.TimeoutException:
                self._log_dct({
                    "event": "error",
                    "request_id": request_id,
                    "error": "upstream_timeout",
                })
                raise HTTPException(504, "Upstream timeout")
            except Exception as e:
                self._log_dct({
                    "event": "error",
                    "request_id": request_id,
                    "error": str(e),
                })
                raise HTTPException(502, f"Upstream error: {e}")
    
    def _log_dct(self, entry: Dict[str, Any]):
        """Append to DCT audit log."""
        if not self.dct_enabled:
            return
            
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.dct_path), exist_ok=True)
            
            # Create hash chain
            entry["hash"] = hashlib.sha256(
                json.dumps(entry, sort_keys=True).encode()
            ).hexdigest()
            
            with open(self.dct_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"DCT log failed: {e}")
    
    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """Run the proxy server."""
        import uvicorn
        uvicorn.run(self.app, host=host, port=port)


def create_app(
    upstream_url: str = None,
    upstream_auth: str = None,
    acc_enabled: bool = False,
    dct_enabled: bool = True,
    dct_path: str = "/data/audit/dct.jsonl",
) -> FastAPI:
    """Factory function for creating the proxy app."""
    
    upstream = upstream_url or os.environ.get("UPSTREAM_URL", "http://localhost:18802")
    auth = upstream_auth or os.environ.get("UPSTREAM_AUTH", "")
    
    proxy = LLMProxy(
        upstream_url=upstream,
        upstream_auth=auth,
        acc_enabled=acc_enabled or os.environ.get("ACC_ENABLED", "").lower() == "true",
        dct_enabled=dct_enabled,
        dct_path=os.environ.get("DCT_PATH", dct_path),
    )
    
    return proxy.app
