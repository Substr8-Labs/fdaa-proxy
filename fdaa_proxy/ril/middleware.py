"""
RIL Middleware

FastAPI middleware that automatically enforces runtime integrity:
- CIA validates every request with messages
- Triggers fire on key events
- Ledger tracks execution state
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from .cia import ContextIntegrityAdapter, RepairMode, RepairResult
from .triggers import TriggerEngine, TriggerEvent
from .ledger import WorkLedger

logger = logging.getLogger("fdaa-ril")


@dataclass
class RILConfig:
    """Configuration for RIL middleware."""
    enabled: bool = True
    cia_mode: RepairMode = RepairMode.PERMISSIVE
    triggers_enabled: bool = True
    ledger_enabled: bool = True
    ledger_path: str = "./data/work_ledger.db"
    
    # Paths to apply CIA validation (supports wildcards)
    cia_paths: list = None
    
    def __post_init__(self):
        if self.cia_paths is None:
            self.cia_paths = [
                "/v1/messages",
                "/api/v1/messages",
                "/governed/call",
                "/chat/completions",
            ]


class RILState:
    """
    RIL state container for the application.
    
    Holds CIA, Triggers, and Ledger instances.
    """
    
    def __init__(self, config: RILConfig):
        self.config = config
        self.cia = ContextIntegrityAdapter(mode=config.cia_mode)
        
        self.ledger = None
        if config.ledger_enabled:
            self.ledger = WorkLedger(path=Path(config.ledger_path))
        
        self.triggers = None
        if config.triggers_enabled:
            self.triggers = TriggerEngine(ledger=self.ledger)
        
        logger.info(f"RIL initialized: CIA={config.cia_mode.value}, "
                   f"Triggers={config.triggers_enabled}, "
                   f"Ledger={config.ledger_enabled}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get combined RIL statistics."""
        return {
            "cia": self.cia.get_stats(),
            "triggers": self.triggers.get_stats() if self.triggers else None,
            "ledger": self.ledger.get_stats() if self.ledger else None,
        }


class RILMiddleware(BaseHTTPMiddleware):
    """
    Runtime Integrity Layer middleware.
    
    Intercepts requests and:
    1. Validates message arrays via CIA
    2. Repairs corrupted contexts
    3. Fires triggers on events
    4. Tracks work in ledger
    """
    
    def __init__(self, app, ril_state: RILState):
        super().__init__(app)
        self.ril = ril_state
    
    def _should_validate(self, path: str) -> bool:
        """Check if this path should have CIA validation."""
        for pattern in self.ril.config.cia_paths:
            if pattern.endswith("*"):
                if path.startswith(pattern[:-1]):
                    return True
            elif path == pattern or path.startswith(pattern + "/"):
                return True
        return False
    
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
            # Not JSON, pass through
            return await call_next(request)
        
        # Extract messages array
        messages = None
        if isinstance(data, dict):
            messages = data.get("messages")
        
        if not messages or not isinstance(messages, list):
            # No messages to validate
            return await call_next(request)
        
        # === CIA VALIDATION ===
        repair_result: Optional[RepairResult] = None
        
        try:
            repaired_messages, repair_result = self.ril.cia.process(messages)
            
            if repair_result.repairs_applied:
                # Context was repaired
                logger.info(f"CIA repaired context: {len(repair_result.repairs_applied)} fixes")
                
                # Fire trigger
                if self.ril.triggers:
                    run_id = data.get("run_id", "unknown")
                    agent_ref = data.get("agent_ref", "unknown")
                    self.ril.triggers.fire_context_repaired(
                        run_id=run_id,
                        agent_ref=agent_ref,
                        repair_result=repair_result.to_dict(),
                    )
                
                # Update request body with repaired messages
                data["messages"] = repaired_messages
                data["_ril_repair"] = repair_result.to_dict()
                
                # Create new request with modified body
                # Note: FastAPI doesn't support modifying request body easily,
                # so we inject into request.state for downstream handlers
                request.state.ril_repaired = True
                request.state.ril_messages = repaired_messages
                request.state.ril_repair_result = repair_result
            
            # Add RIL metadata to request state
            request.state.ril_validated = True
            request.state.ril_original_hash = repair_result.original_hash
            
        except Exception as e:
            logger.error(f"CIA error: {e}")
            # Continue without validation on error (fail-open for now)
            request.state.ril_error = str(e)
        
        # === CALL NEXT ===
        response = await call_next(request)
        
        # === POST-RESPONSE TRIGGERS ===
        # Note: We can add response-based triggers here if needed
        
        return response


def create_ril_state(
    enabled: bool = True,
    cia_mode: str = "permissive",
    triggers_enabled: bool = True,
    ledger_enabled: bool = True,
    ledger_path: str = "./data/work_ledger.db",
) -> RILState:
    """Factory function to create RIL state from environment/config."""
    config = RILConfig(
        enabled=enabled,
        cia_mode=RepairMode(cia_mode),
        triggers_enabled=triggers_enabled,
        ledger_enabled=ledger_enabled,
        ledger_path=ledger_path,
    )
    return RILState(config)


def setup_ril(app, config: Optional[RILConfig] = None) -> RILState:
    """
    Setup RIL middleware on a FastAPI app.
    
    Usage:
        from fdaa_proxy.ril import setup_ril, RILConfig
        
        config = RILConfig(cia_mode=RepairMode.PERMISSIVE)
        ril = setup_ril(app, config)
    """
    import os
    
    if config is None:
        # Load from environment
        config = RILConfig(
            enabled=os.environ.get("RIL_ENABLED", "true").lower() == "true",
            cia_mode=RepairMode(os.environ.get("RIL_CIA_MODE", "permissive")),
            triggers_enabled=os.environ.get("RIL_TRIGGERS", "true").lower() == "true",
            ledger_enabled=os.environ.get("RIL_LEDGER", "true").lower() == "true",
            ledger_path=os.environ.get("RIL_LEDGER_PATH", "./data/work_ledger.db"),
        )
    
    ril_state = RILState(config)
    app.add_middleware(RILMiddleware, ril_state=ril_state)
    app.state.ril = ril_state
    
    # Add RIL stats endpoint
    from fastapi import APIRouter
    ril_router = APIRouter(prefix="/ril", tags=["ril"])
    
    @ril_router.get("/status")
    async def ril_status():
        """Get RIL status and statistics."""
        return {
            "enabled": config.enabled,
            "cia_mode": config.cia_mode.value,
            "triggers_enabled": config.triggers_enabled,
            "ledger_enabled": config.ledger_enabled,
            "stats": ril_state.get_stats(),
        }
    
    @ril_router.get("/ledger/in-progress")
    async def ril_in_progress():
        """Get in-progress work items."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        return {"items": [i.to_dict() for i in ril_state.ledger.get_in_progress()]}
    
    @ril_router.get("/ledger/recoverable")
    async def ril_recoverable():
        """Get recoverable work items."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        return {"items": [i.to_dict() for i in ril_state.ledger.get_recoverable()]}
    
    @ril_router.get("/ledger/run/{run_id}")
    async def ril_run_history(run_id: str):
        """Get full history for a run."""
        if not ril_state.ledger:
            return {"error": "Ledger not enabled"}
        return ril_state.ledger.get_run_history(run_id)
    
    app.include_router(ril_router)
    
    logger.info("RIL middleware installed")
    return ril_state
