"""
GAM Trigger Engine

Event-driven triggers for automatic memory capture.
Fires on execution events and logs to the v2 ledger.

Note: In v2 architecture, most trigger logic is handled by middleware.
This module provides the TriggerEngine for backward compatibility
and for programmatic trigger firing outside of middleware context.
"""

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone

from .ledger_v2 import WorkLedgerV2, EventType, make_turn_id, hash_payload

logger = logging.getLogger("fdaa-ril-triggers")


# Re-export EventType as TriggerEvent for backward compatibility
TriggerEvent = EventType


@dataclass
class TriggerContext:
    """Context passed to trigger handlers."""
    event: EventType
    timestamp: str
    turn_id: str
    agent_ref: str
    session_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "timestamp": self.timestamp,
            "turn_id": self.turn_id,
            "agent_ref": self.agent_ref,
            "session_id": self.session_id,
            "data": self.data,
        }


@dataclass
class TriggerResult:
    """Result of a trigger firing."""
    event: EventType
    success: bool
    event_id: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "success": self.success,
            "event_id": self.event_id,
            "error": self.error,
        }


# Type for custom trigger handlers
TriggerHandler = Callable[[TriggerContext], TriggerResult]


# Attention scores by event type
DEFAULT_ATTENTION = {
    EventType.MESSAGE_RECEIVED: 0.1,
    EventType.TOOL_INVOKED: 0.3,
    EventType.TOOL_COMPLETED: 0.4,
    EventType.TURN_COMPLETED: 0.4,
    EventType.DECISION_POINT: 0.8,
    EventType.CRASH_RECOVERY: 0.95,
    EventType.CONTEXT_REPAIRED: 0.95,
    EventType.CAPABILITY_DENIED: 0.9,
}


class TriggerEngine:
    """
    Manages event-driven triggers for automatic memory capture.
    
    In v2 architecture:
    - Most triggers are fired automatically by middleware
    - TriggerEngine is for programmatic use outside middleware
    - All events go to the v2 ledger with canonical IDs
    """
    
    def __init__(
        self,
        ledger: Optional[WorkLedgerV2] = None,
        enabled_events: Optional[List[EventType]] = None,
    ):
        self.ledger = ledger
        
        # Default: enable all events
        self.enabled_events = enabled_events or list(EventType)
        
        # Custom handlers per event
        self.handlers: Dict[EventType, List[TriggerHandler]] = {}
        
        # Stats
        self.stats = {
            "total_fired": 0,
            "successful": 0,
            "failed": 0,
            "by_event": {e.value: 0 for e in EventType},
        }
    
    def register_handler(self, event: EventType, handler: TriggerHandler):
        """Register a custom handler for an event."""
        if event not in self.handlers:
            self.handlers[event] = []
        self.handlers[event].append(handler)
    
    def is_enabled(self, event: EventType) -> bool:
        """Check if an event is enabled."""
        return event in self.enabled_events
    
    def fire(
        self,
        event: EventType,
        turn_id: str,
        agent_ref: str,
        data: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        attention: Optional[float] = None,
    ) -> TriggerResult:
        """
        Fire a trigger event.
        
        Logs to the v2 ledger with canonical IDs.
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
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data=data or {},
        )
        
        self.stats["total_fired"] += 1
        self.stats["by_event"][event.value] += 1
        
        try:
            # Log to ledger if available
            event_id = None
            if self.ledger:
                # Use default attention if not specified
                if attention is None:
                    attention = DEFAULT_ATTENTION.get(event, 0.5)
                
                logged_event = self.ledger.log_event(
                    turn_id=turn_id,
                    event_type=event,
                    payload=data,
                    agent_ref=agent_ref,
                    session_id=session_id,
                    attention=attention,
                )
                event_id = logged_event.event_id
            
            # Run custom handlers
            for handler in self.handlers.get(event, []):
                try:
                    handler(ctx)
                except Exception as e:
                    logger.error(f"Trigger handler error: {e}")
            
            self.stats["successful"] += 1
            logger.debug(f"Trigger fired: {event.value} ({event_id})")
            
            return TriggerResult(
                event=event,
                success=True,
                event_id=event_id,
            )
        
        except Exception as e:
            self.stats["failed"] += 1
            logger.error(f"Trigger failed: {event.value} - {e}")
            
            return TriggerResult(
                event=event,
                success=False,
                error=str(e),
            )
    
    # === Convenience Methods ===
    
    def fire_tool_invoked(
        self,
        turn_id: str,
        agent_ref: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire tool_invoked event."""
        return self.fire(
            event=EventType.TOOL_INVOKED,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            data={
                "tool": tool_name,
                "tool_use_id": tool_use_id,
                "input": tool_input,
            },
        )
    
    def fire_tool_completed(
        self,
        turn_id: str,
        agent_ref: str,
        tool_name: str,
        tool_output: Any,
        tool_use_id: str,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire tool_completed event."""
        # Increase attention if there was an error
        attention = 0.7 if error else 0.4
        
        return self.fire(
            event=EventType.TOOL_COMPLETED,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=attention,
            data={
                "tool": tool_name,
                "tool_use_id": tool_use_id,
                "output": tool_output,
                "duration_ms": duration_ms,
                "error": error,
            },
        )
    
    def fire_context_repaired(
        self,
        turn_id: str,
        agent_ref: str,
        repair_result: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire context_repaired event (always high attention)."""
        return self.fire(
            event=EventType.CONTEXT_REPAIRED,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=0.95,
            data=repair_result,
        )
    
    def fire_capability_denied(
        self,
        turn_id: str,
        agent_ref: str,
        tool_name: str,
        reason: str,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire capability_denied event (high attention)."""
        return self.fire(
            event=EventType.CAPABILITY_DENIED,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=0.9,
            data={
                "tool": tool_name,
                "reason": reason,
            },
        )
    
    def fire_crash_recovery(
        self,
        turn_id: str,
        agent_ref: str,
        pending_tools: List[Dict[str, Any]],
        pending_tasks: List[Dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire crash_recovery event (always high attention)."""
        return self.fire(
            event=EventType.CRASH_RECOVERY,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=0.95,
            data={
                "pending_tools": pending_tools,
                "pending_tasks": pending_tasks,
            },
        )
    
    def fire_decision_point(
        self,
        turn_id: str,
        agent_ref: str,
        decision: str,
        context: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> TriggerResult:
        """Fire decision_point event (high attention)."""
        return self.fire(
            event=EventType.DECISION_POINT,
            turn_id=turn_id,
            agent_ref=agent_ref,
            session_id=session_id,
            attention=0.8,
            data={
                "decision": decision,
                "context": context,
            },
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trigger statistics."""
        return self.stats.copy()
