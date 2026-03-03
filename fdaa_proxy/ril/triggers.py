"""
GAM Trigger Engine

Automatic memory capture at key execution events.
Fires triggers that commit to the audit ledger and optionally to GAM.
"""

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone

logger = logging.getLogger("fdaa-ril-triggers")


class TriggerEvent(str, Enum):
    """Events that can fire triggers."""
    MESSAGE_RECEIVED = "message_received"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"
    TURN_COMPLETED = "turn_completed"
    DECISION_POINT = "decision_point"
    CRASH_RECOVERY = "crash_recovery"
    CONTEXT_REPAIRED = "context_repaired"
    CAPABILITY_DENIED = "capability_denied"


@dataclass
class TriggerContext:
    """Context passed to trigger handlers."""
    event: TriggerEvent
    timestamp: str
    run_id: str
    agent_ref: str
    session_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "agent_ref": self.agent_ref,
            "session_id": self.session_id,
            "data": self.data,
        }


@dataclass
class TriggerResult:
    """Result of a trigger firing."""
    event: TriggerEvent
    success: bool
    commit_hash: Optional[str] = None
    memory_id: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "success": self.success,
            "commit_hash": self.commit_hash,
            "memory_id": self.memory_id,
            "error": self.error,
        }


# Type for trigger handlers
TriggerHandler = Callable[[TriggerContext], TriggerResult]


class TriggerEngine:
    """
    Manages event-driven triggers for automatic memory capture.
    
    Triggers fire on execution events and create commits/memories.
    """
    
    def __init__(
        self,
        ledger=None,  # WorkLedger instance
        gam_client=None,  # Optional GAM client for memory commits
        enabled_events: Optional[List[TriggerEvent]] = None,
    ):
        self.ledger = ledger
        self.gam_client = gam_client
        
        # Default: enable all auto-fire events
        self.enabled_events = enabled_events or [
            TriggerEvent.MESSAGE_RECEIVED,
            TriggerEvent.TOOL_INVOKED,
            TriggerEvent.TOOL_COMPLETED,
            TriggerEvent.TURN_COMPLETED,
            TriggerEvent.CRASH_RECOVERY,
            TriggerEvent.CONTEXT_REPAIRED,
            TriggerEvent.CAPABILITY_DENIED,
        ]
        
        # Custom handlers per event
        self.handlers: Dict[TriggerEvent, List[TriggerHandler]] = {}
        
        # Stats
        self.stats = {
            "total_fired": 0,
            "successful": 0,
            "failed": 0,
            "by_event": {e.value: 0 for e in TriggerEvent},
        }
    
    def register_handler(self, event: TriggerEvent, handler: TriggerHandler):
        """Register a custom handler for an event."""
        if event not in self.handlers:
            self.handlers[event] = []
        self.handlers[event].append(handler)
    
    def is_enabled(self, event: TriggerEvent) -> bool:
        """Check if an event is enabled."""
        return event in self.enabled_events
    
    def fire(
        self,
        event: TriggerEvent,
        run_id: str,
        agent_ref: str,
        data: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """
        Fire a trigger event.
        
        Creates a ledger entry and optionally a GAM memory.
        """
        if not self.is_enabled(event):
            return TriggerResult(
                event=event,
                success=False,
                error=f"Event {event.value} is not enabled",
            )
        
        timestamp = datetime.now(timezone.utc).isoformat()
        
        ctx = TriggerContext(
            event=event,
            timestamp=timestamp,
            run_id=run_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data=data or {},
        )
        
        self.stats["total_fired"] += 1
        self.stats["by_event"][event.value] += 1
        
        try:
            # Log to ledger if available
            commit_hash = None
            if self.ledger:
                commit_hash = self.ledger.log_trigger(ctx)
            
            # Run custom handlers
            handler_results = []
            if event in self.handlers:
                for handler in self.handlers[event]:
                    try:
                        result = handler(ctx)
                        handler_results.append(result)
                    except Exception as e:
                        logger.error(f"Trigger handler error: {e}")
            
            # Log to GAM if available
            memory_id = None
            if self.gam_client and event in [
                TriggerEvent.DECISION_POINT,
                TriggerEvent.TURN_COMPLETED,
            ]:
                # Only commit significant events to long-term memory
                try:
                    memory_id = self.gam_client.remember(
                        content=f"[{event.value}] {data}",
                        tags=["trigger", event.value],
                        run_id=run_id,
                    )
                except Exception as e:
                    logger.warning(f"GAM commit failed: {e}")
            
            self.stats["successful"] += 1
            
            logger.debug(f"Trigger fired: {event.value} for run {run_id}")
            
            return TriggerResult(
                event=event,
                success=True,
                commit_hash=commit_hash,
                memory_id=memory_id,
            )
        
        except Exception as e:
            self.stats["failed"] += 1
            logger.error(f"Trigger failed: {event.value} - {e}")
            
            return TriggerResult(
                event=event,
                success=False,
                error=str(e),
            )
    
    def fire_tool_invoked(
        self,
        run_id: str,
        agent_ref: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Convenience method for tool_invoked event."""
        return self.fire(
            event=TriggerEvent.TOOL_INVOKED,
            run_id=run_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data={
                "tool": tool_name,
                "input": tool_input,
            },
        )
    
    def fire_tool_completed(
        self,
        run_id: str,
        agent_ref: str,
        tool_name: str,
        tool_output: Any,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Convenience method for tool_completed event."""
        return self.fire(
            event=TriggerEvent.TOOL_COMPLETED,
            run_id=run_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data={
                "tool": tool_name,
                "output": tool_output,
                "duration_ms": duration_ms,
                "error": error,
            },
        )
    
    def fire_context_repaired(
        self,
        run_id: str,
        agent_ref: str,
        repair_result: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Convenience method for context_repaired event."""
        return self.fire(
            event=TriggerEvent.CONTEXT_REPAIRED,
            run_id=run_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data=repair_result,
        )
    
    def fire_capability_denied(
        self,
        run_id: str,
        agent_ref: str,
        tool_name: str,
        reason: str,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Convenience method for capability_denied event."""
        return self.fire(
            event=TriggerEvent.CAPABILITY_DENIED,
            run_id=run_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data={
                "tool": tool_name,
                "reason": reason,
            },
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trigger statistics."""
        return self.stats.copy()
