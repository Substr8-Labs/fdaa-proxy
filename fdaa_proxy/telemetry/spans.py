"""
Proxy-specific span helpers.

Creates structured spans for proxy operations with DCT correlation.
"""

import logging
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass
from contextlib import contextmanager

from .tracer import get_tracer

logger = logging.getLogger(__name__)


class SpanKind(Enum):
    """Types of proxy spans."""
    
    CONNECTION = "connection"
    HANDSHAKE = "handshake"
    REQUEST = "request"
    RESPONSE = "response"
    POLICY_CHECK = "policy_check"
    DCT_LOG = "dct_log"


@dataclass
class ProxySpan:
    """
    Helper for creating proxy-specific spans with DCT correlation.
    
    Usage:
        with ProxySpan.connection(client_id="abc") as span:
            span.set_attribute("custom", "value")
            # do work
    """
    
    @staticmethod
    @contextmanager
    def connection(
        client_id: str,
        session_id: Optional[str] = None,
        dct_entry_id: Optional[str] = None,
    ):
        """Create a span for client connection lifecycle."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span(
            "proxy.connection",
            attributes={
                "proxy.span_kind": SpanKind.CONNECTION.value,
                "proxy.client_id": client_id,
                "proxy.session_id": session_id or "",
                "dct.entry_id": dct_entry_id or "",
            }
        ) as span:
            yield span
    
    @staticmethod
    @contextmanager
    def handshake(
        client_id: str,
        phase: str,  # "challenge" | "connect" | "hello"
        dct_entry_id: Optional[str] = None,
    ):
        """Create a span for handshake phases."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span(
            f"proxy.handshake.{phase}",
            attributes={
                "proxy.span_kind": SpanKind.HANDSHAKE.value,
                "proxy.client_id": client_id,
                "proxy.handshake_phase": phase,
                "dct.entry_id": dct_entry_id or "",
            }
        ) as span:
            yield span
    
    @staticmethod
    @contextmanager
    def request(
        client_id: str,
        method: str,
        request_id: str,
        dct_entry_id: Optional[str] = None,
    ):
        """Create a span for proxied requests."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span(
            f"proxy.request.{method}",
            attributes={
                "proxy.span_kind": SpanKind.REQUEST.value,
                "proxy.client_id": client_id,
                "proxy.method": method,
                "proxy.request_id": request_id,
                "dct.entry_id": dct_entry_id or "",
            }
        ) as span:
            yield span
    
    @staticmethod
    @contextmanager
    def policy_check(
        client_id: str,
        method: str,
        policy_name: str,
        dct_entry_id: Optional[str] = None,
    ):
        """Create a span for policy evaluation."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span(
            f"proxy.policy.{policy_name}",
            attributes={
                "proxy.span_kind": SpanKind.POLICY_CHECK.value,
                "proxy.client_id": client_id,
                "proxy.method": method,
                "proxy.policy_name": policy_name,
                "dct.entry_id": dct_entry_id or "",
            }
        ) as span:
            yield span
    
    @staticmethod
    @contextmanager
    def dct_log(
        event_type: str,
        entry_id: str,
        prev_hash: Optional[str] = None,
    ):
        """Create a span for DCT logging operations."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span(
            f"dct.log.{event_type}",
            attributes={
                "proxy.span_kind": SpanKind.DCT_LOG.value,
                "dct.event_type": event_type,
                "dct.entry_id": entry_id,
                "dct.prev_hash": prev_hash or "",
            }
        ) as span:
            yield span


def add_dct_correlation(span, dct_entry: Dict[str, Any]):
    """
    Add DCT entry correlation to a span.
    
    Links the OTEL trace to the DCT audit chain for provable telemetry.
    """
    if span is None:
        return
    
    try:
        span.set_attribute("dct.entry_id", dct_entry.get("id", ""))
        span.set_attribute("dct.hash", dct_entry.get("hash", ""))
        span.set_attribute("dct.prev_hash", dct_entry.get("prev_hash", ""))
        span.set_attribute("dct.event_type", dct_entry.get("event_type", ""))
        span.set_attribute("dct.timestamp", str(dct_entry.get("timestamp", "")))
    except Exception as e:
        logger.debug(f"Failed to add DCT correlation: {e}")


def get_trace_context() -> Dict[str, str]:
    """
    Get current trace context for propagation.
    
    Returns trace_id and span_id for embedding in DCT entries.
    """
    try:
        from opentelemetry import trace
        
        span = trace.get_current_span()
        if span is None:
            return {}
        
        ctx = span.get_span_context()
        if ctx is None or not ctx.is_valid:
            return {}
        
        return {
            "trace_id": format(ctx.trace_id, '032x'),
            "span_id": format(ctx.span_id, '016x'),
        }
    except Exception:
        return {}
