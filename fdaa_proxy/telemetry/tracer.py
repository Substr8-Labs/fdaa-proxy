"""
OpenTelemetry Tracer Configuration

Initializes OTEL with OTLP exporter for distributed tracing.
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports to handle missing dependencies gracefully
_tracer = None
_initialized = False


@dataclass
class TracingConfig:
    """Configuration for OpenTelemetry tracing."""
    
    service_name: str = "fdaa-proxy"
    service_version: str = "0.1.0"
    
    # OTLP exporter settings
    otlp_endpoint: Optional[str] = None  # e.g., "http://localhost:4317"
    otlp_insecure: bool = True
    
    # Sampling
    sample_rate: float = 1.0  # 1.0 = trace everything
    
    # Console exporter for debugging
    console_export: bool = False
    
    @classmethod
    def from_env(cls) -> "TracingConfig":
        """Load config from environment variables."""
        return cls(
            service_name=os.getenv("OTEL_SERVICE_NAME", "fdaa-proxy"),
            service_version=os.getenv("FDAA_PROXY_VERSION", "0.1.0"),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            otlp_insecure=os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true",
            sample_rate=float(os.getenv("OTEL_SAMPLE_RATE", "1.0")),
            console_export=os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true",
        )


def init_telemetry(config: Optional[TracingConfig] = None) -> bool:
    """
    Initialize OpenTelemetry tracing.
    
    Returns True if initialization succeeded, False if OTEL not available.
    """
    global _tracer, _initialized
    
    if _initialized:
        return True
    
    config = config or TracingConfig.from_env()
    
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
        
        # Create resource with service info
        resource = Resource.create({
            SERVICE_NAME: config.service_name,
            SERVICE_VERSION: config.service_version,
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })
        
        # Create tracer provider
        provider = TracerProvider(resource=resource)
        
        # Add OTLP exporter if endpoint configured
        if config.otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                
                otlp_exporter = OTLPSpanExporter(
                    endpoint=config.otlp_endpoint,
                    insecure=config.otlp_insecure,
                )
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info(f"OTEL: OTLP exporter configured → {config.otlp_endpoint}")
            except ImportError:
                logger.warning("OTEL: OTLP exporter not available (missing grpc dependencies)")
        
        # Add console exporter for debugging
        if config.console_export:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OTEL: Console exporter enabled")
        
        # Set as global tracer provider
        trace.set_tracer_provider(provider)
        
        # Get tracer instance
        _tracer = trace.get_tracer(config.service_name, config.service_version)
        _initialized = True
        
        logger.info(f"OTEL: Telemetry initialized for {config.service_name}")
        return True
        
    except ImportError as e:
        logger.warning(f"OTEL: OpenTelemetry not available ({e})")
        return False
    except Exception as e:
        logger.error(f"OTEL: Failed to initialize ({e})")
        return False


def get_tracer():
    """
    Get the configured tracer instance.
    
    Returns a no-op tracer if OTEL not initialized.
    """
    global _tracer
    
    if _tracer is not None:
        return _tracer
    
    # Return no-op tracer
    try:
        from opentelemetry import trace
        return trace.get_tracer("fdaa-proxy-noop")
    except ImportError:
        return NoOpTracer()


class NoOpTracer:
    """Fallback tracer when OTEL not available."""
    
    def start_span(self, name, **kwargs):
        return NoOpSpan()
    
    def start_as_current_span(self, name, **kwargs):
        return NoOpSpanContext()


class NoOpSpan:
    """No-op span for when tracing is disabled."""
    
    def set_attribute(self, key, value):
        pass
    
    def set_status(self, status):
        pass
    
    def record_exception(self, exception):
        pass
    
    def end(self):
        pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass


class NoOpSpanContext:
    """Context manager for no-op spans."""
    
    def __enter__(self):
        return NoOpSpan()
    
    def __exit__(self, *args):
        pass
