"""
RIL Middleware v2

FastAPI middleware that automatically enforces runtime integrity:
- CIA validates every request with messages
- Events logged to v2 ledger with canonical IDs
- Tool transactions tracked for crash recovery
- Background promotion worker syncs to GAM

Architecture:
    Request → CIA → Ledger (hot) → [Promotion] → GAM (cold)
"""

import os
import json
import asyncio
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .cia import ContextIntegrityAdapter, RepairMode, RepairResult
from .ledger_v2 import (
    WorkLedgerV2,
    EventType,
    ToolTxnStatus,
    make_turn_id,
    hash_payload,
)
from .promotion import (
    PromotionWorker,
    GAMRepo,
    GAMRepoConfig,
    create_promotion_worker,
)

logger = logging.getLogger("fdaa-ril")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class RILConfig:
    """Configuration for RIL middleware."""
    enabled: bool = True
    cia_mode: RepairMode = RepairMode.PERMISSIVE
    
    # Ledger
    ledger_enabled: bool = True
    ledger_path: str = "./data/work_ledger_v2.db"
    
    # GAM promotion
    gam_enabled: bool = True
    gam_repo_path: str = "./data/gam_repo"
    gam_remote_url: Optional[str] = None
    gam_push_on_commit: bool = False
    
    # Promotion worker
    promotion_enabled: bool = True
    promotion_interval_seconds: int = 60  # Run every minute
    promotion_batch_size: int = 50
    
    # DCT receipts
    dct_path: str = "./data/dct_receipts.jsonl"
    
    # Paths to apply CIA validation
    cia_paths: List[str] = field(default_factory=lambda: [
        "/v1/messages",
        "/api/v1/messages",
        "/governed/call",
        "/chat/completions",
    ])


# =============================================================================
# RIL State Container
# =============================================================================

class RILState:
    """
    RIL state container for the application.
    
    Manages:
    - CIA (Context Integrity Adapter)
    - Ledger v2 (canonical IDs, tool transactions)
    - GAM Repo (git-native memory)
    - Promotion Worker (RIL → GAM sync)
    """
    
    def __init__(self, config: RILConfig):
        self.config = config
        
        # CIA
        self.cia = ContextIntegrityAdapter(mode=config.cia_mode)
        
        # Ledger v2
        self.ledger: Optional[WorkLedgerV2] = None
        if config.ledger_enabled:
            self.ledger = WorkLedgerV2(path=Path(config.ledger_path))
        
        # GAM Repo
        self.gam_repo: Optional[GAMRepo] = None
        if config.gam_enabled:
            gam_config = GAMRepoConfig(
                repo_path=Path(config.gam_repo_path),
                remote_url=config.gam_remote_url,
                push_on_commit=config.gam_push_on_commit,
            )
            self.gam_repo = GAMRepo(gam_config)
        
        # Promotion Worker
        self.promotion_worker: Optional[PromotionWorker] = None
        if config.promotion_enabled and self.ledger and self.gam_repo:
            self.promotion_worker = PromotionWorker(
                ledger=self.ledger,
                gam_repo=self.gam_repo,
                dct_path=Path(config.dct_path),
            )
        
        # Background promotion task
        self._promotion_task: Optional[asyncio.Task] = None
        self._promotion_stop = threading.Event()
        
        logger.info(
            f"RIL v2 initialized: CIA={config.cia_mode.value}, "
            f"Ledger={config.ledger_enabled}, GAM={config.gam_enabled}, "
            f"Promotion={config.promotion_enabled}"
        )
    
    def start_promotion_worker(self):
        """Start background promotion worker."""
        if not self.promotion_worker:
            logger.warning("Promotion worker not configured")
            return
        
        self._promotion_stop.clear()
        
        async def promotion_loop():
            while not self._promotion_stop.is_set():
                try:
                    stats = self.promotion_worker.run_batch(
                        limit=self.config.promotion_batch_size
                    )
                    if stats["promoted"] > 0 or stats["errors"] > 0:
                        logger.info(f"Promotion batch: {stats}")
                except Exception as e:
                    logger.error(f"Promotion error: {e}")
                
                await asyncio.sleep(self.config.promotion_interval_seconds)
        
        self._promotion_task = asyncio.create_task(promotion_loop())
        logger.info(f"Promotion worker started (interval={self.config.promotion_interval_seconds}s)")
    
    def stop_promotion_worker(self):
        """Stop background promotion worker."""
        self._promotion_stop.set()
        if self._promotion_task:
            self._promotion_task.cancel()
            self._promotion_task = None
        logger.info("Promotion worker stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get combined RIL statistics."""
        stats = {
            "cia": self.cia.get_stats(),
            "ledger": self.ledger.get_stats() if self.ledger else None,
            "promotion": self.promotion_worker.get_stats() if self.promotion_worker else None,
        }
        
        if self.gam_repo:
            try:
                stats["gam_head"] = self.gam_repo.get_head_sha()[:8]
            except Exception:
                stats["gam_head"] = None
        
        return stats


# =============================================================================
# Middleware
# =============================================================================

class RILMiddleware(BaseHTTPMiddleware):
    """
    Runtime Integrity Layer middleware.
    
    Intercepts requests and:
    1. Validates message arrays via CIA
    2. Repairs corrupted contexts
    3. Logs events to ledger with canonical IDs
    4. Tracks tool transactions for crash recovery
    """
    
    def __init__(self, app, ril_state: RILState):
        super().__init__(app)
        self.ril = ril_state
        self._turn_counter: Dict[str, int] = {}  # session_id → turn count
    
    def _should_validate(self, path: str) -> bool:
        """Check if this path should have CIA validation."""
        for pattern in self.ril.config.cia_paths:
            if pattern.endswith("*"):
                if path.startswith(pattern[:-1]):
                    return True
            elif path == pattern or path.startswith(pattern + "/"):
                return True
        return False
    
    def _get_turn_id(self, session_id: str, messages: List[Dict]) -> str:
        """Generate a turn_id for this request."""
        # Increment turn counter for this session
        if session_id not in self._turn_counter:
            self._turn_counter[session_id] = 0
        self._turn_counter[session_id] += 1
        turn_index = self._turn_counter[session_id]
        
        # Hash the last user message
        user_msg_hash = "empty"
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg_hash = hash_payload(msg.get("content", ""))[:16]
                break
        
        return make_turn_id(session_id, turn_index, user_msg_hash)
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through RIL."""
        
        if not self.ril.config.enabled:
            return await call_next(request)
        
        path = request.url.path
        method = request.method
        
        # Only validate POST/PUT requests to relevant paths
        if method not in ["POST", "PUT"] or not self._should_validate(path):
            return await call_next(request)
        
        # Read and parse body
        try:
            body = await request.body()
            if not body:
                return await call_next(request)
            
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return await call_next(request)
        
        # Extract messages array
        messages = data.get("messages") if isinstance(data, dict) else None
        if not messages or not isinstance(messages, list):
            return await call_next(request)
        
        # Extract session info
        session_id = data.get("session_id") or data.get("conversation_id") or "default"
        agent_ref = data.get("agent_ref") or data.get("model") or "unknown"
        
        # Generate turn_id
        turn_id = self._get_turn_id(session_id, messages)
        
        # === CIA VALIDATION ===
        repair_result: Optional[RepairResult] = None
        
        try:
            repaired_messages, repair_result = self.ril.cia.process(messages)
            
            if repair_result.repairs_applied:
                logger.info(f"CIA repaired context: {len(repair_result.repairs_applied)} fixes")
                
                # Log context_repaired event (high attention - always promoted)
                if self.ril.ledger:
                    event = self.ril.ledger.log_event(
                        turn_id=turn_id,
                        event_type=EventType.CONTEXT_REPAIRED,
                        payload=repair_result.to_dict(),
                        agent_ref=agent_ref,
                        session_id=session_id,
                        attention=0.95,  # High - always promote
                    )
                    
                    # Mark any affected tool transactions as synthetic-failed
                    for repair in repair_result.repairs_applied:
                        if repair.get("type") == "injected_synthetic_failure":
                            tool_use_id = repair.get("tool_use_id")
                            if tool_use_id:
                                txn = self.ril.ledger.get_tool_txn_by_use_id(tool_use_id)
                                if txn:
                                    self.ril.ledger.mark_synthetic_failed(
                                        txn.tool_txn_id,
                                        "Context repair - synthetic failure injected"
                                    )
                    
                    # Immediate promotion for high-integrity events
                    if self.ril.promotion_worker:
                        self.ril.promotion_worker.promote_immediate(event)
                
                # Store in request state
                request.state.ril_repaired = True
                request.state.ril_messages = repaired_messages
                request.state.ril_repair_result = repair_result
                data["messages"] = repaired_messages
            
            request.state.ril_validated = True
            request.state.ril_original_hash = repair_result.original_hash
            request.state.ril_turn_id = turn_id
            
        except Exception as e:
            logger.error(f"CIA error: {e}")
            request.state.ril_error = str(e)
        
        # === LOG MESSAGE RECEIVED ===
        if self.ril.ledger:
            self.ril.ledger.log_event(
                turn_id=turn_id,
                event_type=EventType.MESSAGE_RECEIVED,
                payload={"path": path, "message_count": len(messages)},
                agent_ref=agent_ref,
                session_id=session_id,
                attention=0.1,  # Low - rarely promoted
            )
        
        # === TRACK TOOL INVOCATIONS ===
        # Check for tool_use blocks in the last assistant message
        if self.ril.ledger:
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_use_id = block.get("id")
                                tool_name = block.get("name", "unknown")
                                tool_input = block.get("input", {})
                                
                                # Start tracking this tool transaction
                                self.ril.ledger.start_tool_txn(
                                    turn_id=turn_id,
                                    tool_use_id=tool_use_id,
                                    tool_name=tool_name,
                                    input_data=tool_input,
                                )
                                
                                # Log tool_invoked event
                                self.ril.ledger.log_event(
                                    turn_id=turn_id,
                                    event_type=EventType.TOOL_INVOKED,
                                    payload={
                                        "tool": tool_name,
                                        "tool_use_id": tool_use_id,
                                    },
                                    agent_ref=agent_ref,
                                    session_id=session_id,
                                    attention=0.3,
                                )
                    break  # Only check the last assistant message
        
        # === CALL NEXT ===
        response = await call_next(request)
        
        # === LOG TURN COMPLETED ===
        if self.ril.ledger:
            self.ril.ledger.log_event(
                turn_id=turn_id,
                event_type=EventType.TURN_COMPLETED,
                payload={
                    "status_code": response.status_code,
                    "path": path,
                },
                agent_ref=agent_ref,
                session_id=session_id,
                attention=0.4,
            )
        
        return response


# =============================================================================
# Factory Functions
# =============================================================================

def create_ril_state(
    enabled: bool = True,
    cia_mode: str = "permissive",
    ledger_enabled: bool = True,
    ledger_path: str = "./data/work_ledger_v2.db",
    gam_enabled: bool = True,
    gam_repo_path: str = "./data/gam_repo",
    gam_remote_url: Optional[str] = None,
    promotion_enabled: bool = True,
    promotion_interval_seconds: int = 60,
) -> RILState:
    """Factory function to create RIL state from parameters."""
    config = RILConfig(
        enabled=enabled,
        cia_mode=RepairMode(cia_mode),
        ledger_enabled=ledger_enabled,
        ledger_path=ledger_path,
        gam_enabled=gam_enabled,
        gam_repo_path=gam_repo_path,
        gam_remote_url=gam_remote_url,
        promotion_enabled=promotion_enabled,
        promotion_interval_seconds=promotion_interval_seconds,
    )
    return RILState(config)


def setup_ril(app, config: Optional[RILConfig] = None) -> RILState:
    """
    Setup RIL middleware on a FastAPI app.
    
    Usage:
        from fdaa_proxy.ril import setup_ril, RILConfig
        
        ril = setup_ril(app)
    """
    if config is None:
        # Load from environment
        config = RILConfig(
            enabled=os.environ.get("RIL_ENABLED", "true").lower() == "true",
            cia_mode=RepairMode(os.environ.get("RIL_CIA_MODE", "permissive")),
            ledger_enabled=os.environ.get("RIL_LEDGER", "true").lower() == "true",
            ledger_path=os.environ.get("RIL_LEDGER_PATH", "./data/work_ledger_v2.db"),
            gam_enabled=os.environ.get("RIL_GAM", "true").lower() == "true",
            gam_repo_path=os.environ.get("RIL_GAM_PATH", "./data/gam_repo"),
            gam_remote_url=os.environ.get("RIL_GAM_REMOTE"),
            gam_push_on_commit=os.environ.get("RIL_GAM_PUSH", "false").lower() == "true",
            promotion_enabled=os.environ.get("RIL_PROMOTION", "true").lower() == "true",
            promotion_interval_seconds=int(os.environ.get("RIL_PROMOTION_INTERVAL", "60")),
        )
    
    ril_state = RILState(config)
    app.add_middleware(RILMiddleware, ril_state=ril_state)
    app.state.ril = ril_state
    
    # === API ENDPOINTS ===
    from fastapi import APIRouter
    ril_router = APIRouter(prefix="/ril", tags=["ril"])
    
    @ril_router.get("/status")
    async def ril_status():
        """Get RIL status and statistics."""
        return {
            "version": 2,
            "enabled": config.enabled,
            "cia_mode": config.cia_mode.value,
            "ledger_enabled": config.ledger_enabled,
            "gam_enabled": config.gam_enabled,
            "promotion_enabled": config.promotion_enabled,
            "stats": ril_state.get_stats(),
        }
    
    @ril_router.get("/events")
    async def ril_events(limit: int = 50, promotion_state: Optional[str] = None):
        """Get recent events from ledger."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        
        # Get events for promotion (which returns by attention desc)
        events = ril_state.ledger.get_events_for_promotion(limit=limit)
        return {"events": [e.to_dict() for e in events]}
    
    @ril_router.get("/events/promoted")
    async def ril_promoted_events(limit: int = 50):
        """Get promoted events."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        
        # Query promoted events directly
        conn = ril_state.ledger._conn()
        cursor = conn.execute("""
            SELECT * FROM events
            WHERE promotion_state = 'promoted'
            ORDER BY event_ts DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        return {"events": [ril_state.ledger._event_from_row(r).to_dict() for r in rows]}
    
    @ril_router.get("/tool-transactions")
    async def ril_tool_txns(status: Optional[str] = None):
        """Get tool transactions."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        
        if status == "pending":
            txns = ril_state.ledger.get_pending_tool_txns()
        else:
            # Get all recent
            conn = ril_state.ledger._conn()
            cursor = conn.execute("""
                SELECT * FROM tool_transactions
                ORDER BY created_at DESC
                LIMIT 100
            """)
            rows = cursor.fetchall()
            conn.close()
            txns = [ril_state.ledger._txn_from_row(r) for r in rows]
        
        return {"transactions": [t.to_dict() for t in txns]}
    
    @ril_router.get("/session/{session_id}")
    async def ril_session_history(session_id: str):
        """Get full history for a session."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        return ril_state.ledger.get_session_history(session_id)
    
    @ril_router.post("/promotion/run")
    async def ril_promotion_run(limit: int = 50):
        """Manually trigger a promotion batch."""
        if not ril_state.promotion_worker:
            return {"error": "Promotion not enabled"}
        
        stats = ril_state.promotion_worker.run_batch(limit=limit)
        return {"status": "ok", "stats": stats}
    
    @ril_router.get("/gam/log")
    async def ril_gam_log(limit: int = 20):
        """Get recent GAM commits."""
        if not ril_state.gam_repo:
            return {"error": "GAM not enabled"}
        
        try:
            log = ril_state.gam_repo._run_git("log", "--oneline", f"-{limit}")
            commits = [line.split(" ", 1) for line in log.strip().split("\n") if line]
            return {"commits": [{"sha": c[0], "message": c[1] if len(c) > 1 else ""} for c in commits]}
        except Exception as e:
            return {"error": str(e)}
    
    app.include_router(ril_router)
    
    # === LIFECYCLE HOOKS ===
    @app.on_event("startup")
    async def start_promotion():
        if config.promotion_enabled:
            ril_state.start_promotion_worker()
    
    @app.on_event("shutdown")
    async def stop_promotion():
        ril_state.stop_promotion_worker()
    
    logger.info("RIL v2 middleware installed")
    return ril_state
