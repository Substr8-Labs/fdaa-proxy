"""
LLM HTTP Reverse Proxy

Sits in front of Anthropic-compatible APIs (like OpenClaw Bridge)
to provide full FDAA platform services:
- ACC: Agent Capability Certificate validation (ED25519)
- DCT: Delegation Chain Tracking (hash-chained audit)
- GAM: Git-native Agent Memory (commit after each interaction)
"""

import os
import json
import time
import hashlib
import logging
import subprocess
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

logger = logging.getLogger("fdaa-proxy.llm")


class LLMProxy:
    """
    HTTP reverse proxy for LLM APIs with full FDAA platform services.
    
    Routes:
        POST /v1/messages -> upstream (Anthropic-compatible)
        GET /health -> health check
        GET /audit -> recent DCT entries
    """
    
    def __init__(
        self,
        upstream_url: str,
        upstream_auth: Optional[str] = None,
        # ACC config
        acc_enabled: bool = False,
        acc_public_key_path: Optional[str] = None,
        acc_dev_mode: bool = False,
        # DCT config  
        dct_enabled: bool = True,
        dct_path: str = "/data/audit/dct.jsonl",
        # GAM config
        gam_enabled: bool = False,
        gam_repo_path: Optional[str] = None,
        gam_auto_commit: bool = True,
    ):
        self.upstream_url = upstream_url.rstrip("/")
        self.upstream_auth = upstream_auth
        
        # ACC
        self.acc_enabled = acc_enabled
        self.acc_validator = None
        if acc_enabled:
            self._init_acc(acc_public_key_path, acc_dev_mode)
        
        # DCT
        self.dct_enabled = dct_enabled
        self.dct_path = dct_path
        self.dct_chain_hash = None  # Previous hash for chain
        
        # GAM
        self.gam_enabled = gam_enabled
        self.gam_repo_path = gam_repo_path
        self.gam_auto_commit = gam_auto_commit
        if gam_enabled and gam_repo_path:
            self._init_gam()
        
        self.app = FastAPI(title="FDAA LLM Proxy")
        self._setup_routes()
        self._setup_middleware()
        
        # Stats
        self.requests_total = 0
        self.requests_blocked = 0
        self.acc_validations = 0
        self.gam_commits = 0
    
    def _init_acc(self, public_key_path: Optional[str], dev_mode: bool):
        """Initialize ACC validator."""
        try:
            from ..acc.validator import ACCValidator
            self.acc_validator = ACCValidator(
                public_key_path=public_key_path,
                dev_mode=dev_mode,
            )
            logger.info(f"ACC initialized (dev_mode={dev_mode})")
        except Exception as e:
            logger.error(f"ACC init failed: {e}")
            self.acc_enabled = False
    
    def _init_gam(self):
        """Initialize GAM repository."""
        try:
            repo = Path(self.gam_repo_path)
            if not (repo / ".git").exists():
                subprocess.run(
                    ["git", "init"],
                    cwd=repo,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "fdaa-proxy@substr8labs.com"],
                    cwd=repo,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "FDAA Proxy"],
                    cwd=repo,
                    capture_output=True,
                )
                logger.info(f"GAM: Initialized git repo at {repo}")
            else:
                # Mark as safe directory for Docker volumes
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", str(repo)],
                    capture_output=True,
                )
                logger.info(f"GAM: Using existing repo at {repo}")
        except Exception as e:
            logger.error(f"GAM init failed: {e}")
            self.gam_enabled = False
    
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
                "version": "1.0.0",
                "upstream": self.upstream_url,
                "platform_services": {
                    "acc": {"enabled": self.acc_enabled, "validations": self.acc_validations},
                    "dct": {"enabled": self.dct_enabled, "path": self.dct_path},
                    "gam": {"enabled": self.gam_enabled, "commits": self.gam_commits},
                },
                "stats": {
                    "requests_total": self.requests_total,
                    "requests_blocked": self.requests_blocked,
                }
            }
        
        @self.app.get("/audit")
        async def get_audit(limit: int = 50):
            """Get recent DCT audit entries."""
            try:
                with open(self.dct_path, "r") as f:
                    lines = f.readlines()
                entries = [json.loads(line) for line in lines[-limit:]]
                return {"entries": entries, "total": len(lines)}
            except FileNotFoundError:
                return {"entries": [], "total": 0}
        
        @self.app.post("/v1/messages")
        async def proxy_messages(
            request: Request,
            authorization: Optional[str] = Header(None),
            x_acc_token: Optional[str] = Header(None),
            x_customer_id: Optional[str] = Header(None),
            x_session_id: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None),
        ):
            """Proxy Anthropic-compatible /v1/messages with full FDAA governance."""
            
            self.requests_total += 1
            start_time = time.time()
            request_id = x_request_id or self._generate_id("req")
            
            # Get request body
            try:
                body = await request.json()
            except Exception as e:
                raise HTTPException(400, f"Invalid JSON: {e}")
            
            # === ACC VALIDATION ===
            acc_subject = None
            if self.acc_enabled:
                self.acc_validations += 1
                
                if not x_acc_token:
                    self.requests_blocked += 1
                    self._log_dct({
                        "event": "blocked",
                        "reason": "missing_acc_token",
                        "request_id": request_id,
                    })
                    raise HTTPException(401, "X-ACC-Token header required")
                
                # Validate token
                result = self.acc_validator.validate(x_acc_token)
                if not result.valid:
                    self.requests_blocked += 1
                    self._log_dct({
                        "event": "blocked",
                        "reason": "invalid_acc_token",
                        "error": result.error,
                        "request_id": request_id,
                    })
                    raise HTTPException(403, f"ACC validation failed: {result.error}")
                
                acc_subject = result.token.subject if result.token else None
                
                # Check for llm:invoke capability
                if result.token and not result.token.has_capability("llm:invoke"):
                    self.requests_blocked += 1
                    self._log_dct({
                        "event": "blocked",
                        "reason": "missing_capability",
                        "required": "llm:invoke",
                        "request_id": request_id,
                    })
                    raise HTTPException(403, "ACC token missing 'llm:invoke' capability")
                
                logger.info(f"ACC validated: {acc_subject}")
            
            # === DCT: LOG REQUEST ===
            if self.dct_enabled:
                self._log_dct({
                    "event": "request",
                    "request_id": request_id,
                    "customer_id": x_customer_id,
                    "session_id": x_session_id,
                    "acc_subject": acc_subject,
                    "model": body.get("model"),
                    "messages_count": len(body.get("messages", [])),
                    "max_tokens": body.get("max_tokens"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            
            # === FORWARD TO UPSTREAM ===
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    headers = {"Content-Type": "application/json"}
                    
                    if self.upstream_auth:
                        headers["Authorization"] = f"Bearer {self.upstream_auth}"
                    
                    response = await client.post(
                        f"{self.upstream_url}/v1/messages",
                        json=body,
                        headers=headers,
                    )
                    
                    result = response.json()
                    latency_ms = int((time.time() - start_time) * 1000)
                    
                    # Extract response text for GAM
                    response_text = None
                    if result.get("content"):
                        for block in result["content"]:
                            if block.get("type") == "text":
                                response_text = block.get("text")
                                break
                    
                    # === DCT: LOG RESPONSE ===
                    if self.dct_enabled:
                        dct_entry = self._log_dct({
                            "event": "response",
                            "request_id": request_id,
                            "customer_id": x_customer_id,
                            "session_id": x_session_id,
                            "status": response.status_code,
                            "model": result.get("model"),
                            "usage": result.get("usage"),
                            "latency_ms": latency_ms,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    
                    # === GAM: COMMIT MEMORY ===
                    if self.gam_enabled and self.gam_auto_commit and x_customer_id:
                        user_message = None
                        messages = body.get("messages", [])
                        if messages:
                            last_user = [m for m in messages if m.get("role") == "user"]
                            if last_user:
                                user_message = last_user[-1].get("content", "")
                        
                        self._gam_commit(
                            customer_id=x_customer_id,
                            session_id=x_session_id,
                            request_id=request_id,
                            user_message=user_message,
                            assistant_response=response_text,
                            model=result.get("model"),
                            usage=result.get("usage"),
                        )
                    
                    # Add FDAA metadata to response
                    result["_fdaa"] = {
                        "request_id": request_id,
                        "latency_ms": latency_ms,
                        "acc_validated": self.acc_enabled,
                        "dct_logged": self.dct_enabled,
                        "gam_committed": self.gam_enabled and x_customer_id is not None,
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
    
    def _generate_id(self, prefix: str = "fdaa") -> str:
        """Generate unique ID."""
        return f"{prefix}_{hashlib.sha256(f'{time.time()}{self.requests_total}'.encode()).hexdigest()[:16]}"
    
    def _log_dct(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Append to DCT audit log with hash chain."""
        if not self.dct_enabled:
            return entry
            
        try:
            os.makedirs(os.path.dirname(self.dct_path), exist_ok=True)
            
            # Add chain hash
            entry["prev_hash"] = self.dct_chain_hash
            entry["hash"] = hashlib.sha256(
                json.dumps(entry, sort_keys=True).encode()
            ).hexdigest()
            self.dct_chain_hash = entry["hash"]
            
            with open(self.dct_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            
            return entry
        except Exception as e:
            logger.error(f"DCT log failed: {e}")
            return entry
    
    def _gam_commit(
        self,
        customer_id: str,
        session_id: Optional[str],
        request_id: str,
        user_message: Optional[str],
        assistant_response: Optional[str],
        model: Optional[str],
        usage: Optional[Dict],
    ):
        """Commit interaction to GAM repository."""
        if not self.gam_enabled or not self.gam_repo_path:
            return
        
        try:
            repo = Path(self.gam_repo_path)
            timestamp = datetime.now(timezone.utc)
            
            # Create customer directory
            customer_dir = repo / "customers" / customer_id
            customer_dir.mkdir(parents=True, exist_ok=True)
            
            # Append to memory log
            memory_file = customer_dir / "memory.md"
            memory_entry = f"""
## {timestamp.strftime('%Y-%m-%d %H:%M')} UTC
- **Request:** {request_id}
- **Session:** {session_id or 'N/A'}
- **Model:** {model or 'unknown'}
- **User:** {(user_message or '')[:150]}{'...' if user_message and len(user_message) > 150 else ''}
- **Assistant:** {(assistant_response or '')[:200]}{'...' if assistant_response and len(assistant_response) > 200 else ''}
- **Tokens:** {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out

"""
            with open(memory_file, "a") as f:
                f.write(memory_entry)
            
            # Git commit
            commit_msg = f"gam: {customer_id} - {request_id}"
            
            subprocess.run(
                ["git", "add", str(customer_dir)],
                cwd=repo,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", commit_msg, "--allow-empty"],
                cwd=repo,
                capture_output=True,
            )
            
            self.gam_commits += 1
            logger.info(f"GAM: Committed {commit_msg}")
            
        except Exception as e:
            logger.error(f"GAM commit failed: {e}")
    
    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """Run the proxy server."""
        import uvicorn
        logger.info(f"Starting FDAA LLM Proxy on {host}:{port}")
        logger.info(f"  Upstream: {self.upstream_url}")
        logger.info(f"  ACC: {'enabled' if self.acc_enabled else 'disabled'}")
        logger.info(f"  DCT: {'enabled' if self.dct_enabled else 'disabled'} ({self.dct_path})")
        logger.info(f"  GAM: {'enabled' if self.gam_enabled else 'disabled'} ({self.gam_repo_path})")
        uvicorn.run(self.app, host=host, port=port)


def create_app(
    upstream_url: str = None,
    upstream_auth: str = None,
    acc_enabled: bool = False,
    acc_public_key_path: str = None,
    acc_dev_mode: bool = False,
    dct_enabled: bool = True,
    dct_path: str = "/data/audit/dct.jsonl",
    gam_enabled: bool = False,
    gam_repo_path: str = None,
) -> FastAPI:
    """Factory function for creating the proxy app."""
    
    upstream = upstream_url or os.environ.get("UPSTREAM_URL", "http://localhost:18802")
    auth = upstream_auth or os.environ.get("UPSTREAM_AUTH", "")
    
    proxy = LLMProxy(
        upstream_url=upstream,
        upstream_auth=auth,
        acc_enabled=acc_enabled or os.environ.get("ACC_ENABLED", "").lower() == "true",
        acc_public_key_path=acc_public_key_path or os.environ.get("ACC_PUBLIC_KEY_PATH"),
        acc_dev_mode=acc_dev_mode or os.environ.get("ACC_DEV_MODE", "").lower() == "true",
        dct_enabled=dct_enabled,
        dct_path=os.environ.get("DCT_PATH", dct_path),
        gam_enabled=gam_enabled or os.environ.get("GAM_ENABLED", "").lower() == "true",
        gam_repo_path=gam_repo_path or os.environ.get("GAM_REPO_PATH"),
    )
    
    return proxy.app
