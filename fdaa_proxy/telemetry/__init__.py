"""
FDAA Proxy Telemetry Module

OpenTelemetry integration for distributed tracing and observability.
Links OTEL traces to DCT audit chain for provable telemetry.
"""

from .tracer import init_telemetry, get_tracer, TracingConfig
from .spans import ProxySpan, SpanKind, add_dct_correlation, get_trace_context

__all__ = [
    "init_telemetry",
    "get_tracer", 
    "TracingConfig",
    "ProxySpan",
    "SpanKind",
    "add_dct_correlation",
    "get_trace_context",
]
