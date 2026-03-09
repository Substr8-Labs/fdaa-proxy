"""
MCP Server - Substr8 Governance Plane

Exposes Substr8 governance tools (ACC, DCT, GAM) via the Model Context Protocol,
allowing any compatible agent framework to connect.
"""

import asyncio
import json
import hashlib
import os
import time
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import uuid
import sqlite3

# FastAPI for HTTP transport (MCP can use stdio or HTTP)
try:
    from fastapi import FastAPI, HTTPException, Depends, Header
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


class APIKeyManager:
    """
    Manages API key validation and tiers.
    
    Tiers:
    - free: 100 requests/minute
    - pro: 1000 requests/minute
    - enterprise: unlimited
    """
    
    def __init__(self, keys_file: Optional[str] = None):
        self.keys: Dict[str, Dict[str, Any]] = {}
        self.keys_file = keys_file
        self._load_keys()
    
    def _load_keys(self):
        """Load API keys from file or environment."""
        # Load from environment
        env_key = os.environ.get("SUBSTR8_API_KEY")
        if env_key:
            self.keys[env_key] = {
                "tier": "pro",
                "project_id": "default",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        
        # Load from file
        if self.keys_file and os.path.exists(self.keys_file):
            try:
                with open(self.keys_file) as f:
                    data = json.load(f)
                    self.keys.update(data.get("keys", {}))
            except Exception:
                pass
        
        # Load from default location
        default_file = os.path.expanduser("~/.substr8/api_keys.json")
        if os.path.exists(default_file):
            try:
                with open(default_file) as f:
                    data = json.load(f)
                    self.keys.update(data.get("keys", {}))
            except Exception:
                pass
    
    def validate(self, api_key: Optional[str]) -> Dict[str, Any]:
        """
        Validate an API key and return its metadata.
        
        Returns:
            {"valid": True/False, "tier": str, "project_id": str, "error": str}
        """
        if not api_key:
            return {"valid": False, "error": "Missing API key", "tier": None}
        
        # Strip "Bearer " prefix if present
        if api_key.startswith("Bearer "):
            api_key = api_key[7:]
        
        # Check key
        if api_key in self.keys:
            key_data = self.keys[api_key]
            return {
                "valid": True,
                "tier": key_data.get("tier", "free"),
                "project_id": key_data.get("project_id", "default"),
                "error": None
            }
        
        return {"valid": False, "error": "Invalid API key", "tier": None}
    
    def generate_key(self, tier: str = "free", project_id: str = "default") -> str:
        """Generate a new API key."""
        key = f"sk-substr8-{secrets.token_urlsafe(32)}"
        self.keys[key] = {
            "tier": tier,
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        return key
    
    def get_rate_limit(self, tier: str) -> int:
        """Get requests per minute for a tier."""
        limits = {
            "free": 100,
            "pro": 1000,
            "enterprise": 10000,  # effectively unlimited
        }
        return limits.get(tier, 100)


class RateLimiter:
    """
    Simple in-memory rate limiter using sliding window.
    """
    
    def __init__(self):
        # key -> list of timestamps
        self.requests: Dict[str, List[float]] = defaultdict(list)
        self.window_seconds = 60  # 1 minute window
    
    def check(self, key: str, limit: int) -> Dict[str, Any]:
        """
        Check if request is allowed under rate limit.
        
        Returns:
            {"allowed": True/False, "remaining": int, "reset_in": int}
        """
        now = time.time()
        window_start = now - self.window_seconds
        
        # Clean old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]
        
        current_count = len(self.requests[key])
        
        if current_count >= limit:
            # Find when oldest request expires
            oldest = min(self.requests[key]) if self.requests[key] else now
            reset_in = int(oldest + self.window_seconds - now)
            return {
                "allowed": False,
                "remaining": 0,
                "reset_in": max(1, reset_in),
                "limit": limit
            }
        
        # Record this request
        self.requests[key].append(now)
        
        return {
            "allowed": True,
            "remaining": limit - current_count - 1,
            "reset_in": self.window_seconds,
            "limit": limit
        }


@dataclass
class Run:
    """A governed execution run."""
    run_id: str
    project_id: str
    agent_ref: str
    agent_hash: Optional[str]
    policy_ref: str
    policy_hash: str
    metadata: Dict[str, Any]
    created_at: str
    ended_at: Optional[str] = None
    entries: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    """MCP Server configuration."""
    host: str = "127.0.0.1"
    port: int = 3456
    api_key: Optional[str] = None
    project_id: Optional[str] = None
    policy_path: Optional[str] = None
    local_mode: bool = False
    control_plane_url: str = "https://mcp.substr8labs.com"
    # CIA audit database (for cia.* tools)
    cia_audit_db: Optional[str] = None
    cia_status_url: str = "http://localhost:18800/status"
    # Auth settings
    require_auth: bool = False  # If True, require API key for all requests
    api_keys_file: Optional[str] = None  # Path to API keys JSON file
    rate_limiting: bool = True  # Enable rate limiting


class CIAAuditService:
    """
    CIA Audit Service - reads from DCT audit database.
    
    Exposes CIA data as an audit surface (read-only).
    Does NOT touch OAuth/subscription auth paths.
    """
    
    def __init__(self, db_path: Optional[str] = None, status_url: str = "http://localhost:18800/status"):
        self.db_path = db_path
        self.status_url = status_url
        self._status_cache = None
        self._status_cache_time = 0
    
    def get_status(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get CIA status.
        
        Returns enabled, mode, version, provider_path.
        """
        import time
        
        # Try live status endpoint first (with 5s cache)
        now = time.time()
        if self._status_cache and (now - self._status_cache_time) < 5:
            status = self._status_cache
        else:
            try:
                import httpx
                resp = httpx.get(self.status_url, timeout=2.0)
                if resp.status_code == 200:
                    status = resp.json()
                    self._status_cache = status
                    self._status_cache_time = now
                else:
                    status = None
            except Exception:
                status = None
        
        if status and "cia" in status:
            return {
                "enabled": status["cia"].get("enabled", False),
                "mode": status["cia"].get("mode", "unknown"),
                "cia_version": "1.0.0",
                "provider_path": status.get("auth", {}).get("mode", "direct"),
                "scope": f"run:{run_id}" if run_id else "global",
                "stats": status["cia"].get("stats", {})
            }
        
        # Fallback: read from DB if available
        return {
            "enabled": self.db_path is not None,
            "mode": "permissive",
            "cia_version": "1.0.0",
            "provider_path": "unknown",
            "scope": f"run:{run_id}" if run_id else "global"
        }
    
    def get_report(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get CIA integrity report (summary).
        
        Returns total_validated, repairs, blocked, etc.
        """
        # Try live status for global stats
        status = self.get_status(run_id)
        
        if "stats" in status:
            stats = status["stats"]
            return {
                "scope": status.get("scope", "global"),
                "total_validated": stats.get("total_validated", 0),
                "valid": stats.get("valid", 0),
                "repaired": stats.get("repaired", 0),
                "rejected": stats.get("rejected", 0),
                "mode": status.get("mode", "unknown")
            }
        
        # Query DB for run-specific or historical data
        if self.db_path:
            return self._query_report_from_db(run_id)
        
        return {
            "scope": f"run:{run_id}" if run_id else "global",
            "total_validated": 0,
            "valid": 0,
            "repaired": 0,
            "rejected": 0,
            "mode": "unknown",
            "note": "No CIA audit data available"
        }
    
    def get_repairs(self, run_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        """
        Get itemized repair list.
        
        Each repair: {seq, timestamp, reason_code, original_hash, repaired_hash, severity}
        """
        if not self.db_path:
            return {"repairs": [], "note": "No audit database configured"}
        
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Query cia_repair events
            query = """
                SELECT id, timestamp, arguments, result, entry_hash
                FROM dct_entries
                WHERE event_type = 'cia_repair'
                ORDER BY timestamp DESC
                LIMIT ?
            """
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            conn.close()
            
            repairs = []
            for i, row in enumerate(rows):
                try:
                    result = json.loads(row["result"]) if row["result"] else {}
                    args = json.loads(row["arguments"]) if row["arguments"] else {}
                    
                    # Only include repairs that actually did something
                    repairs_applied = result.get("repairs_applied", [])
                    if repairs_applied:
                        for repair in repairs_applied:
                            repairs.append({
                                "seq": i,
                                "timestamp": row["timestamp"],
                                "reason_code": repair.get("type", "unknown"),
                                "original_hash": result.get("original_hash", ""),
                                "repaired_hash": result.get("repaired_hash", ""),
                                "severity": "warning" if "synthetic" in repair.get("type", "") else "info",
                                "request_id": args.get("request_id"),
                                "model": args.get("model")
                            })
                except (json.JSONDecodeError, KeyError):
                    continue
            
            return {
                "repairs": repairs,
                "total_in_db": len(rows),
                "repairs_with_changes": len(repairs)
            }
        
        except Exception as e:
            return {"repairs": [], "error": str(e)}
    
    def get_receipts(self, run_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        """
        Get LLM call receipts (hashes + metadata, NOT content).
        
        Each receipt: {seq, request_sha256, response_sha256, latency_ms, model}
        """
        if not self.db_path:
            return {"receipts": [], "note": "No audit database configured"}
        
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = """
                SELECT id, timestamp, arguments, result, entry_hash
                FROM dct_entries
                WHERE event_type = 'cia_repair'
                ORDER BY timestamp DESC
                LIMIT ?
            """
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            conn.close()
            
            receipts = []
            for i, row in enumerate(rows):
                try:
                    result = json.loads(row["result"]) if row["result"] else {}
                    args = json.loads(row["arguments"]) if row["arguments"] else {}
                    
                    receipts.append({
                        "seq": i,
                        "timestamp": row["timestamp"],
                        "request_sha256": result.get("original_hash", ""),
                        "response_sha256": result.get("repaired_hash", ""),
                        "model": args.get("model", "unknown"),
                        "entry_hash": row["entry_hash"]
                    })
                except (json.JSONDecodeError, KeyError):
                    continue
            
            return {"receipts": receipts}
        
        except Exception as e:
            return {"receipts": [], "error": str(e)}
    
    def _query_report_from_db(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Query report data from SQLite."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) FROM dct_entries WHERE event_type = 'cia_repair'
            """)
            total = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT result FROM dct_entries 
                WHERE event_type = 'cia_repair'
            """)
            
            valid = 0
            repaired = 0
            for row in cursor.fetchall():
                try:
                    result = json.loads(row[0])
                    if result.get("success"):
                        if result.get("repairs_applied"):
                            repaired += 1
                        else:
                            valid += 1
                except:
                    pass
            
            conn.close()
            
            return {
                "scope": f"run:{run_id}" if run_id else "global",
                "total_validated": total,
                "valid": valid,
                "repaired": repaired,
                "rejected": total - valid - repaired,
                "mode": "permissive",
                "source": "dct_audit_db"
            }
        
        except Exception as e:
            return {"error": str(e)}


class Substr8MCPServer:
    """
    MCP Server that bridges external agent frameworks to Substr8 governance.
    """
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.runs: Dict[str, Run] = {}
        self.policies: Dict[str, Dict[str, Any]] = {}
        self._load_default_policy()
        
        # Initialize CIA audit service
        self.cia_service = CIAAuditService(
            db_path=config.cia_audit_db,
            status_url=config.cia_status_url
        )
        
        # Initialize auth and rate limiting
        self.api_key_manager = APIKeyManager(keys_file=config.api_keys_file)
        self.rate_limiter = RateLimiter()
        
        # Add default key in local mode
        if config.local_mode and not self.api_key_manager.keys:
            local_key = self.api_key_manager.generate_key(tier="pro", project_id="local")
            print(f"  Local API key: {local_key}")
        
        if HAS_FASTAPI:
            self.app = self._create_app()
        else:
            self.app = None
    
    def _load_default_policy(self):
        """Load default ACC policy."""
        default_policy = {
            "version": "1.0",
            "capabilities": {
                "allow": [
                    "web_search",
                    "memory_read",
                    "memory_write",
                    "send_message"
                ],
                "deny": [
                    "shell_exec",
                    "file_delete",
                    "system_admin"
                ]
            }
        }
        policy_hash = hashlib.sha256(
            json.dumps(default_policy, sort_keys=True).encode()
        ).hexdigest()
        self.policies["default"] = {
            "policy": default_policy,
            "hash": f"sha256:{policy_hash[:16]}..."
        }
    
    def _create_app(self) -> FastAPI:
        """Create FastAPI app for MCP server."""
        app = FastAPI(
            title="Substr8 MCP Server",
            description="Governance plane for AI agents",
            version="1.0.0"
        )
        
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Auth and rate limiting middleware
        from fastapi import Request
        from fastapi.responses import JSONResponse
        
        @app.middleware("http")
        async def auth_and_rate_limit(request: Request, call_next):
            # Skip auth for health and discovery endpoints
            if request.url.path in ["/health", "/mcp/tools", "/"]:
                return await call_next(request)
            
            # Get API key from header
            api_key = request.headers.get("X-Substr8-Key") or request.headers.get("Authorization")
            
            # In local mode without require_auth, allow all requests
            if self.config.local_mode and not self.config.require_auth:
                response = await call_next(request)
                return response
            
            # Validate API key if auth is required
            if self.config.require_auth:
                auth_result = self.api_key_manager.validate(api_key)
                if not auth_result["valid"]:
                    return JSONResponse(
                        status_code=401,
                        content={"error": auth_result["error"], "code": "UNAUTHORIZED"}
                    )
                tier = auth_result["tier"]
            else:
                # Default to free tier if no auth required but key provided
                if api_key:
                    auth_result = self.api_key_manager.validate(api_key)
                    tier = auth_result.get("tier", "free") if auth_result["valid"] else "free"
                else:
                    tier = "free"
            
            # Rate limiting
            if self.config.rate_limiting:
                # Use API key or IP as rate limit key
                rate_key = api_key or request.client.host if request.client else "unknown"
                limit = self.api_key_manager.get_rate_limit(tier)
                rate_result = self.rate_limiter.check(rate_key, limit)
                
                if not rate_result["allowed"]:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Rate limit exceeded",
                            "code": "RATE_LIMITED",
                            "limit": rate_result["limit"],
                            "reset_in": rate_result["reset_in"]
                        },
                        headers={
                            "X-RateLimit-Limit": str(rate_result["limit"]),
                            "X-RateLimit-Remaining": "0",
                            "X-RateLimit-Reset": str(rate_result["reset_in"])
                        }
                    )
                
                # Add rate limit headers to response
                response = await call_next(request)
                response.headers["X-RateLimit-Limit"] = str(rate_result["limit"])
                response.headers["X-RateLimit-Remaining"] = str(rate_result["remaining"])
                return response
            
            return await call_next(request)
        
        # Tool endpoints
        @app.post("/tools/run/start")
        async def start_run(payload: Dict[str, Any]):
            return self.tool_run_start(payload)
        
        @app.post("/tools/run/end")
        async def end_run(payload: Dict[str, Any]):
            return self.tool_run_end(payload)
        
        @app.post("/tools/policy/check")
        async def policy_check(payload: Dict[str, Any]):
            return self.tool_policy_check(payload)
        
        @app.post("/tools/ledger/append")
        async def ledger_append(payload: Dict[str, Any]):
            return self.tool_ledger_append(payload)
        
        @app.post("/tools/ledger/timeline")
        async def ledger_timeline(payload: Dict[str, Any]):
            return self.tool_audit_timeline(payload)
        
        @app.post("/tools/audit/timeline")
        async def audit_timeline(payload: Dict[str, Any]):
            return self.tool_audit_timeline(payload)
        
        @app.post("/tools/tool/invoke")
        async def tool_invoke(payload: Dict[str, Any]):
            return self.tool_invoke(payload)
        
        @app.post("/tools/verify/run")
        async def verify_run(payload: Dict[str, Any]):
            return self.tool_verify_run(payload)
        
        @app.post("/tools/memory/write")
        async def memory_write(payload: Dict[str, Any]):
            return self.tool_memory_write(payload)
        
        @app.post("/tools/memory/search")
        async def memory_search(payload: Dict[str, Any]):
            return self.tool_memory_search(payload)
        
        @app.post("/tools/web_search")
        async def web_search(payload: Dict[str, Any]):
            return self.tool_web_search(payload)
        
        # === CIA Audit Tools (read-only surface) ===
        
        @app.post("/tools/cia/status")
        async def cia_status(payload: Dict[str, Any]):
            return self.tool_cia_status(payload)
        
        @app.post("/tools/cia/report")
        async def cia_report(payload: Dict[str, Any]):
            return self.tool_cia_report(payload)
        
        @app.post("/tools/cia/repairs")
        async def cia_repairs(payload: Dict[str, Any]):
            return self.tool_cia_repairs(payload)
        
        @app.post("/tools/cia/receipts")
        async def cia_receipts(payload: Dict[str, Any]):
            return self.tool_cia_receipts(payload)
        
        # === Agent Registry Tools (TowerHQ) ===
        
        @app.post("/tools/agent/register")
        async def agent_register(payload: Dict[str, Any]):
            return self.tool_agent_register(payload)
        
        @app.post("/tools/agent/verify")
        async def agent_verify(payload: Dict[str, Any]):
            return self.tool_agent_verify(payload)
        
        @app.post("/tools/agent/lookup")
        async def agent_lookup(payload: Dict[str, Any]):
            return self.tool_agent_lookup(payload)
        
        @app.post("/tools/agent/list")
        async def agent_list(payload: Dict[str, Any]):
            return self.tool_agent_list(payload)
        
        # MCP discovery
        @app.get("/mcp/tools")
        async def list_tools():
            return self.get_tool_definitions()
        
        # Health check
        @app.get("/health")
        async def health():
            return {
                "status": "healthy",
                "version": "1.0.0",
                "runs_active": len([r for r in self.runs.values() if not r.ended_at]),
                "auth_required": self.config.require_auth,
                "rate_limiting": self.config.rate_limiting
            }
        
        # === Admin endpoints ===
        
        @app.post("/admin/keys/generate")
        async def generate_key(payload: Dict[str, Any], x_admin_key: Optional[str] = Header(None)):
            """Generate a new API key. Requires admin key in local mode."""
            if not self.config.local_mode:
                # In production, would require proper admin auth
                raise HTTPException(status_code=403, detail="Admin endpoints disabled")
            
            tier = payload.get("tier", "free")
            project_id = payload.get("project_id", "default")
            key = self.api_key_manager.generate_key(tier=tier, project_id=project_id)
            
            return {
                "api_key": key,
                "tier": tier,
                "project_id": project_id,
                "rate_limit": self.api_key_manager.get_rate_limit(tier)
            }
        
        @app.get("/admin/keys/list")
        async def list_keys(x_admin_key: Optional[str] = Header(None)):
            """List API keys (masked). Local mode only."""
            if not self.config.local_mode:
                raise HTTPException(status_code=403, detail="Admin endpoints disabled")
            
            keys = []
            for key, data in self.api_key_manager.keys.items():
                keys.append({
                    "key_prefix": key[:15] + "...",
                    "tier": data.get("tier"),
                    "project_id": data.get("project_id"),
                    "created_at": data.get("created_at")
                })
            return {"keys": keys, "total": len(keys)}
        
        @app.get("/admin/rate-limits")
        async def get_rate_limits():
            """Get rate limit tiers."""
            return {
                "tiers": {
                    "free": {"requests_per_minute": 100},
                    "pro": {"requests_per_minute": 1000},
                    "enterprise": {"requests_per_minute": 10000}
                }
            }
        
        return app
    
    # === Tool Implementations ===
    
    def tool_run_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a governed run context."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        policy_ref = payload.get("policy_ref", "default")
        policy_data = self.policies.get(policy_ref, self.policies["default"])
        
        run = Run(
            run_id=run_id,
            project_id=payload.get("project_id", self.config.project_id or "default"),
            agent_ref=payload.get("agent_ref", "unknown"),
            agent_hash=payload.get("agent_hash"),
            policy_ref=policy_ref,
            policy_hash=policy_data["hash"],
            metadata=payload.get("metadata", {}),
            created_at=datetime.now(timezone.utc).isoformat()
        )
        self.runs[run_id] = run
        
        return {
            "run_id": run_id,
            "policy_hash": policy_data["hash"],
            "created_at": run.created_at
        }
    
    def tool_run_end(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """End a run and finalize ledger."""
        run_id = payload.get("run_id")
        if run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        
        run = self.runs[run_id]
        run.ended_at = datetime.now(timezone.utc).isoformat()
        
        # Verify chain
        chain_valid = self._verify_chain(run)
        
        return {
            "run_id": run_id,
            "ended_at": run.ended_at,
            "entries": len(run.entries),
            "chain_valid": chain_valid
        }
    
    def tool_policy_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Check ACC policy for an action."""
        run_id = payload.get("run_id")
        action = payload.get("action")
        
        if run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        
        run = self.runs[run_id]
        policy_data = self.policies.get(run.policy_ref, self.policies["default"])
        policy = policy_data["policy"]
        
        # Check policy
        allow_list = policy.get("capabilities", {}).get("allow", [])
        deny_list = policy.get("capabilities", {}).get("deny", [])
        
        if action in deny_list:
            allowed = False
            reason = f"action '{action}' in deny list"
        elif action in allow_list:
            allowed = True
            reason = f"action '{action}' in allow list"
        else:
            # Default: allow with warning
            allowed = True
            reason = f"action '{action}' not in policy (unverified)"
        
        # Log the check
        self._append_entry(run, {
            "type": "policy_check",
            "action": action,
            "allowed": allowed,
            "reason": reason
        })
        
        return {
            "allow": allowed,
            "reason": reason,
            "policy_hash": policy_data["hash"]
        }
    
    def tool_ledger_append(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append entry to DCT ledger."""
        run_id = payload.get("run_id")
        if run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        
        run = self.runs[run_id]
        entry = self._append_entry(run, payload.get("entry", {}))
        
        return {
            "entry_id": entry["entry_id"],
            "entry_hash": entry["hash"],
            "sequence": entry["sequence"]
        }
    
    def tool_audit_timeline(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns the human-debuggable audit trail for a run (DCT-derived).
        
        "Show me the receipts. Show me what happened."
        """
        run_id = payload.get("run_id")
        if not run_id or run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        
        run = self.runs[run_id]
        
        # Format entries for human readability
        formatted_entries = []
        for entry in run.entries:
            formatted = {
                "seq": entry["sequence"],
                "type": entry.get("type"),
                "hash": entry["hash"],
                "prev_hash": entry["prev_hash"]
            }
            # Add type-specific fields
            if entry.get("type") == "policy_check":
                formatted["action"] = entry.get("action")
                formatted["allowed"] = entry.get("allowed")
            elif entry.get("type") in ("tool_call", "tool_denied"):
                formatted["tool"] = entry.get("tool")
                if entry.get("type") == "tool_denied":
                    formatted["allowed"] = False
                    formatted["reason"] = entry.get("reason")
            elif entry.get("type") == "memory_write":
                formatted["memory_id"] = entry.get("memory_id")
                formatted["linked_to"] = entry.get("linked_ledger_hash")
            formatted_entries.append(formatted)
        
        return {
            "run_id": run_id,
            "agent_ref": run.agent_ref,
            "policy_hash": run.policy_hash,
            "started_at": run.created_at,
            "ended_at": run.ended_at,
            "entries": formatted_entries,
            "chain_valid": self._verify_chain(run)
        }
    
    def tool_memory_write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Write memory with provenance.
        
        Requires run_id. Strongly encourages ledger_entry_hash for provenance linking.
        """
        run_id = payload.get("run_id")
        if not run_id or run_id not in self.runs:
            raise ValueError(f"Unknown or missing run_id: {run_id}")
        
        run = self.runs[run_id]
        content = payload.get("content", "")
        memory_type = payload.get("type", "general")
        tags = payload.get("tags", [])
        linked_ledger_hash = payload.get("ledger_entry_hash")  # Provenance link
        
        # Generate memory ID
        memory_id = f"mem-{uuid.uuid4().hex[:12]}"
        commit_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        
        # Log to ledger with provenance link
        entry = self._append_entry(run, {
            "type": "memory_write",
            "memory_id": memory_id,
            "memory_type": memory_type,
            "content_hash": f"sha256:{commit_hash}",
            "tags": tags,
            "linked_ledger_hash": linked_ledger_hash  # Links back to source action
        })
        
        return {
            "memory_id": memory_id,
            "commit": commit_hash,
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "file_path": f"memory/{memory_type}/{tags[0] if tags else 'general'}.md",
            "ledger_entry_hash": entry["hash"],
            "provenance_linked": linked_ledger_hash is not None
        }
    
    def tool_memory_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Search memory with provenance output."""
        # Stub - would integrate with GAM
        return {
            "results": [],
            "query": payload.get("query", ""),
            "provenance": {
                "search_at": datetime.now(timezone.utc).isoformat()
            }
        }
    
    def tool_invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Unified governed tool gateway.
        
        One entry point for any tool: ACC check → DCT log → execute → DCT log result.
        """
        run_id = payload.get("run_id")
        tool = payload.get("tool")
        args = payload.get("args", {})
        
        if not run_id or run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        if not tool:
            raise ValueError("Tool name required")
        
        run = self.runs[run_id]
        
        # Step 1: ACC check
        policy_check = self.tool_policy_check({
            "run_id": run_id,
            "action": tool
        })
        
        if not policy_check["allow"]:
            # Log the denial
            entry = self._append_entry(run, {
                "type": "tool_denied",
                "tool": tool,
                "args": args,
                "reason": policy_check["reason"]
            })
            return {
                "allowed": False,
                "reason": policy_check["reason"],
                "ledger_entry_hash": entry["hash"],
                "policy_decision_hash": policy_check["policy_hash"]
            }
        
        # Step 2: Execute tool
        result = self._execute_tool(tool, args)
        
        # Step 3: Log execution with result
        entry = self._append_entry(run, {
            "type": "tool_call",
            "tool": tool,
            "input": args,
            "output": result,
            "output_hash": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
        })
        
        return {
            "allowed": True,
            "result": result,
            "ledger_entry_hash": entry["hash"],
            "policy_decision_hash": policy_check["policy_hash"]
        }
    
    def _execute_tool(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool and return result. Stub implementations."""
        if tool == "web_search":
            query = args.get("query", "")
            return {
                "items": [
                    {"title": f"Result for: {query}", "url": "https://example.com", "snippet": "..."}
                ]
            }
        elif tool == "send_message":
            return {"status": "sent", "message_id": f"msg-{uuid.uuid4().hex[:8]}"}
        elif tool == "db_query":
            return {"rows": [], "count": 0}
        else:
            return {"status": "executed", "tool": tool}
    
    def tool_verify_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        One-shot verifier: checks chain integrity and provenance links.
        
        This is the "auditor tool" — makes claims cryptographic, not vibes.
        """
        run_id = payload.get("run_id")
        check_provenance = payload.get("check_provenance", True)
        
        if not run_id or run_id not in self.runs:
            raise ValueError(f"Unknown run: {run_id}")
        
        run = self.runs[run_id]
        errors = []
        
        # Check 1: Ledger chain integrity
        chain_valid = True
        prev_hash = "sha256:" + "0" * 64
        for i, entry in enumerate(run.entries):
            if entry["prev_hash"] != prev_hash:
                chain_valid = False
                errors.append(f"Entry {i}: prev_hash mismatch")
            prev_hash = entry["hash"]
        
        # Check 2: Provenance links (memory writes reference valid ledger entries)
        provenance_valid = True
        if check_provenance:
            ledger_hashes = {e["hash"] for e in run.entries}
            memory_writes = [e for e in run.entries if e.get("type") == "memory_write"]
            for mw in memory_writes:
                linked_hash = mw.get("linked_ledger_hash")
                if linked_hash and linked_hash not in ledger_hashes:
                    provenance_valid = False
                    errors.append(f"Memory {mw.get('memory_id')}: invalid ledger link")
        
        return {
            "run_id": run_id,
            "ledger_chain_valid": chain_valid,
            "checked_entries": len(run.entries),
            "provenance_valid": provenance_valid,
            "errors": errors
        }
    
    def tool_web_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Governed web search (ACC + DCT). Wrapper around tool_invoke."""
        return self.tool_invoke({
            "run_id": payload.get("run_id"),
            "tool": "web_search",
            "args": {"query": payload.get("query", "")}
        })
    
    # === CIA Audit Tools (Read-Only Surface) ===
    
    def tool_cia_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get CIA status.
        
        Returns: enabled, mode, cia_version, provider_path, scope
        
        Note: CIA does not proxy subscription/OAuth traffic.
        In OAuth mode, CIA runs as runtime validation or is disabled,
        but status remains auditable.
        """
        run_id = payload.get("run_id")
        return self.cia_service.get_status(run_id)
    
    def tool_cia_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get CIA integrity report (summary).
        
        Returns: total_validated, valid, repaired, rejected, mode
        
        Shows what CIA did without exposing content.
        """
        run_id = payload.get("run_id")
        return self.cia_service.get_report(run_id)
    
    def tool_cia_repairs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get itemized CIA repair list.
        
        Each repair: {seq, timestamp, reason_code, original_hash, repaired_hash, severity}
        
        Hashes only — no raw prompts/responses.
        """
        run_id = payload.get("run_id")
        limit = payload.get("limit", 100)
        return self.cia_service.get_repairs(run_id, limit)
    
    def tool_cia_receipts(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get LLM call receipts (hashes + metadata, NOT content).
        
        Each receipt: {seq, request_sha256, response_sha256, model}
        
        This is how you add "LLM call traceability" without
        intercepting secrets or exposing conversation content.
        """
        run_id = payload.get("run_id")
        limit = payload.get("limit", 100)
        return self.cia_service.get_receipts(run_id, limit)
    
    # === Agent Registry Tools (TowerHQ) ===
    
    def tool_agent_register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Register an agent identity with TowerHQ registry."""
        import requests
        
        registry_url = os.environ.get("AGENT_REGISTRY_URL", "http://localhost:8099")
        
        data = {
            "name": payload.get("name"),
            "version": payload.get("version"),
            "identity_hash": payload.get("identity_hash"),
            "manifest_hash": payload.get("manifest_hash"),
            "framework": payload.get("framework", "custom"),
        }
        
        if payload.get("publisher_name") or payload.get("publisher_org"):
            data["publisher"] = {
                "name": payload.get("publisher_name"),
                "org": payload.get("publisher_org")
            }
        
        try:
            resp = requests.post(f"{registry_url}/api/v1/agents", json=data, timeout=30)
            if resp.status_code == 201:
                return resp.json()
            else:
                return {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            return {"error": str(e)}
    
    def tool_agent_verify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Verify an agent identity hash against the registry."""
        import requests
        
        registry_url = os.environ.get("AGENT_REGISTRY_URL", "http://localhost:8099")
        identity_hash = payload.get("identity_hash")
        
        try:
            resp = requests.post(
                f"{registry_url}/api/v1/verify",
                json={"identity_hash": identity_hash},
                timeout=10
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e), "verified": False}
    
    def tool_agent_lookup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Look up agent details by name."""
        import requests
        
        registry_url = os.environ.get("AGENT_REGISTRY_URL", "http://localhost:8099")
        name = payload.get("name")
        version = payload.get("version")
        
        try:
            if version:
                resp = requests.get(f"{registry_url}/api/v1/agents/{name}/{version}", timeout=10)
            else:
                resp = requests.get(f"{registry_url}/api/v1/agents/{name}", timeout=10)
            
            if resp.status_code == 404:
                return {"error": f"Agent not found: {name}", "found": False}
            return {"found": True, **resp.json()}
        except Exception as e:
            return {"error": str(e), "found": False}
    
    def tool_agent_list(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """List registered agents."""
        import requests
        
        registry_url = os.environ.get("AGENT_REGISTRY_URL", "http://localhost:8099")
        
        params = {"limit": payload.get("limit", 50)}
        if payload.get("org"):
            params["org"] = payload["org"]
        
        try:
            resp = requests.get(f"{registry_url}/api/v1/agents", params=params, timeout=10)
            return resp.json()
        except Exception as e:
            return {"error": str(e), "agents": []}
    
    # === Helper Methods ===
    
    def _append_entry(self, run: Run, data: Dict[str, Any]) -> Dict[str, Any]:
        """Append entry to run's ledger with hash chain."""
        prev_hash = run.entries[-1]["hash"] if run.entries else "sha256:" + "0" * 64
        
        entry = {
            "entry_id": f"e-{uuid.uuid4().hex[:12]}",
            "sequence": len(run.entries),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prev_hash": prev_hash,
            **data
        }
        
        # Compute hash
        entry_bytes = json.dumps(entry, sort_keys=True).encode()
        entry["hash"] = f"sha256:{hashlib.sha256(entry_bytes).hexdigest()}"
        
        run.entries.append(entry)
        return entry
    
    def _verify_chain(self, run: Run) -> bool:
        """Verify hash chain integrity."""
        prev_hash = "sha256:" + "0" * 64
        for entry in run.entries:
            if entry["prev_hash"] != prev_hash:
                return False
            prev_hash = entry["hash"]
        return True
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get MCP tool definitions - the 5 killer tools."""
        return [
            # 1) Run lifecycle
            {
                "name": "substr8.run.start",
                "description": "Create run context + bind identity + policy snapshot",
                "parameters": {
                    "project_id": {"type": "string", "required": True},
                    "agent_ref": {"type": "string", "required": True},
                    "agent_hash": {"type": "string", "optional": True},
                    "policy_ref": {"type": "string", "default": "default"},
                    "metadata": {"type": "object", "optional": True}
                }
            },
            {
                "name": "substr8.run.end",
                "description": "Close run and finalize ledger",
                "parameters": {
                    "run_id": {"type": "string", "required": True}
                }
            },
            # 2) The unified governed tool gateway
            {
                "name": "substr8.tool.invoke",
                "description": "Governed tool gateway: ACC check → DCT log → execute → log result",
                "parameters": {
                    "run_id": {"type": "string", "required": True},
                    "tool": {"type": "string", "required": True},
                    "args": {"type": "object", "optional": True}
                }
            },
            # 3) Memory with provenance
            {
                "name": "substr8.memory.write",
                "description": "Write memory with provenance link to ledger entry",
                "parameters": {
                    "run_id": {"type": "string", "required": True},
                    "type": {"type": "string"},
                    "content": {"type": "string", "required": True},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "ledger_entry_hash": {"type": "string", "description": "Link to source action"}
                }
            },
            # 4) Audit timeline
            {
                "name": "substr8.audit.timeline",
                "description": "Get human-debuggable audit trail for a run",
                "parameters": {
                    "run_id": {"type": "string", "required": True}
                }
            },
            # 5) One-shot verifier
            {
                "name": "substr8.verify.run",
                "description": "Verify chain integrity and provenance links",
                "parameters": {
                    "run_id": {"type": "string", "required": True},
                    "check_provenance": {"type": "boolean", "default": True}
                }
            },
            # Bonus: Policy explain
            {
                "name": "substr8.policy.check",
                "description": "Check ACC policy for an action (with reason)",
                "parameters": {
                    "run_id": {"type": "string", "required": True},
                    "action": {"type": "string", "required": True}
                }
            },
            # Convenience wrappers
            {
                "name": "substr8.memory.search",
                "description": "Search memory with provenance output",
                "parameters": {
                    "query": {"type": "string", "required": True}
                }
            },
            # === CIA Audit Tools (read-only, does not proxy auth) ===
            {
                "name": "substr8.cia.status",
                "description": "CIA status: enabled, mode, version, provider_path",
                "parameters": {
                    "run_id": {"type": "string", "optional": True, "description": "Scope to specific run"}
                }
            },
            {
                "name": "substr8.cia.report",
                "description": "CIA integrity report: validated, repaired, rejected counts",
                "parameters": {
                    "run_id": {"type": "string", "optional": True}
                }
            },
            {
                "name": "substr8.cia.repairs",
                "description": "Itemized repair list (hashes only, no content)",
                "parameters": {
                    "run_id": {"type": "string", "optional": True},
                    "limit": {"type": "integer", "default": 100}
                }
            },
            {
                "name": "substr8.cia.receipts",
                "description": "LLM call receipts (request/response hashes + model)",
                "parameters": {
                    "run_id": {"type": "string", "optional": True},
                    "limit": {"type": "integer", "default": 100}
                }
            },
            # === Agent Registry Tools (TowerHQ) ===
            {
                "name": "substr8.agent.register",
                "description": "Register an agent identity with TowerHQ registry",
                "parameters": {
                    "name": {"type": "string", "required": True},
                    "version": {"type": "string", "required": True},
                    "identity_hash": {"type": "string", "required": True},
                    "manifest_hash": {"type": "string", "required": True},
                    "framework": {"type": "string", "default": "custom"},
                    "publisher_name": {"type": "string", "optional": True},
                    "publisher_org": {"type": "string", "optional": True}
                }
            },
            {
                "name": "substr8.agent.verify",
                "description": "Verify an agent identity hash against the registry",
                "parameters": {
                    "identity_hash": {"type": "string", "required": True}
                }
            },
            {
                "name": "substr8.agent.lookup",
                "description": "Look up agent details by name",
                "parameters": {
                    "name": {"type": "string", "required": True},
                    "version": {"type": "string", "optional": True}
                }
            },
            {
                "name": "substr8.agent.list",
                "description": "List registered agents",
                "parameters": {
                    "org": {"type": "string", "optional": True},
                    "limit": {"type": "integer", "default": 50}
                }
            }
        ]
    
    def run(self):
        """Run the MCP server."""
        if not HAS_FASTAPI:
            raise RuntimeError("FastAPI not installed. Run: pip install fastapi uvicorn")
        
        uvicorn.run(
            self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="info"
        )


def create_server(
    host: str = "127.0.0.1",
    port: int = 3456,
    api_key: Optional[str] = None,
    project_id: Optional[str] = None,
    local_mode: bool = False,
    cia_audit_db: Optional[str] = None,
    cia_status_url: str = "http://localhost:18800/status",
    require_auth: bool = False,
    api_keys_file: Optional[str] = None,
    rate_limiting: bool = True
) -> Substr8MCPServer:
    """Create and return an MCP server instance."""
    # Auto-detect CIA audit database
    if cia_audit_db is None:
        default_db = os.path.expanduser("~/.openclaw/workspace/fdaa-proxy/data/anthropic-audit.db")
        if os.path.exists(default_db):
            cia_audit_db = default_db
    
    config = MCPServerConfig(
        host=host,
        port=port,
        api_key=api_key,
        project_id=project_id,
        local_mode=local_mode,
        cia_audit_db=cia_audit_db,
        cia_status_url=cia_status_url,
        require_auth=require_auth,
        api_keys_file=api_keys_file,
        rate_limiting=rate_limiting
    )
    return Substr8MCPServer(config)
