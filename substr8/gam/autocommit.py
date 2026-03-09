"""
GAM Auto-Commit Integration

Automatic memory commits during agent execution with trace context.

Usage:
    from substr8.gam.autocommit import GAMAutoCommit, observe, decision

    # Initialize with repo path and optional API endpoint
    gam = GAMAutoCommit(repo_path="/path/to/workspace")
    
    # Or use HTTP API
    gam = GAMAutoCommit(api_url="http://localhost:8090")

    # Decorate functions to auto-commit observations
    @observe(gam, "Analyzed user intent")
    async def analyze_intent(message: str) -> dict:
        ...

    # Or commit inline
    gam.observe("User requested feature X", confidence="high")
    gam.decision("Will implement using approach Y", reasoning="Because Z")
"""

import os
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class TraceContext:
    """OpenTelemetry trace context."""
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    
    @classmethod
    def from_env(cls) -> "TraceContext":
        """Load trace context from environment variables."""
        return cls(
            trace_id=os.environ.get("OTEL_TRACE_ID"),
            span_id=os.environ.get("OTEL_SPAN_ID"),
        )
    
    @classmethod
    def from_otel(cls) -> "TraceContext":
        """Extract trace context from current OpenTelemetry span."""
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            ctx = span.get_span_context()
            if ctx.is_valid:
                return cls(
                    trace_id=format(ctx.trace_id, '032x'),
                    span_id=format(ctx.span_id, '016x'),
                )
        except ImportError:
            pass
        return cls()
    
    def to_dict(self) -> dict:
        """Convert to dict for API calls."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
    
    @property
    def is_valid(self) -> bool:
        return self.trace_id is not None


class GAMAutoCommit:
    """
    Automatic memory commits during agent execution.
    
    Can operate in two modes:
    1. Direct repo access (faster, requires git)
    2. HTTP API (network-accessible, trace-aware)
    """
    
    def __init__(
        self,
        repo_path: Optional[str] = None,
        api_url: Optional[str] = None,
        default_source: str = "observation",
        auto_trace: bool = True,
    ):
        """
        Initialize GAM auto-commit.
        
        Args:
            repo_path: Path to GAM repository (for direct access)
            api_url: GAM API URL (for HTTP access)
            default_source: Default source type for memories
            auto_trace: Automatically capture OTEL trace context
        """
        self.repo_path = repo_path
        self.api_url = api_url
        self.default_source = default_source
        self.auto_trace = auto_trace
        
        self._repo = None
        self._trace_context: Optional[TraceContext] = None
        
        if repo_path:
            self._init_repo()
    
    def _init_repo(self):
        """Initialize direct repo access."""
        try:
            from .core import open_gam
            self._repo = open_gam(self.repo_path)
        except Exception as e:
            logger.warning(f"Could not open GAM repo: {e}")
    
    def set_trace_context(self, trace_id: str, span_id: Optional[str] = None):
        """Manually set trace context."""
        self._trace_context = TraceContext(trace_id=trace_id, span_id=span_id)
    
    def get_trace_context(self) -> TraceContext:
        """Get current trace context."""
        if self._trace_context:
            return self._trace_context
        
        if self.auto_trace:
            # Try OTEL first
            ctx = TraceContext.from_otel()
            if ctx.is_valid:
                return ctx
            # Fall back to env vars
            return TraceContext.from_env()
        
        return TraceContext()
    
    @contextmanager
    def span(self, name: str):
        """
        Create a traced span for a block of work.
        
        Usage:
            with gam.span("analyze_request"):
                # All observations here share the span
                gam.observe("Found 3 entities")
        """
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("gam.autocommit")
            with tracer.start_as_current_span(name) as span:
                yield span
        except ImportError:
            # No OTEL, just yield None
            yield None
    
    def observe(
        self,
        content: str,
        title: Optional[str] = None,
        confidence: str = "medium",
        tags: Optional[list[str]] = None,
    ) -> Optional[str]:
        """
        Commit an observation to GAM.
        
        Args:
            content: What was observed
            title: Optional title
            confidence: Confidence level (high/medium/low)
            tags: Optional tags
        
        Returns:
            Memory ID if successful, None otherwise
        """
        return self._commit(
            content=content,
            title=title,
            source="observation",
            confidence=confidence,
            tags=tags,
        )
    
    def decision(
        self,
        content: str,
        reasoning: Optional[str] = None,
        confidence: str = "high",
        tags: Optional[list[str]] = None,
    ) -> Optional[str]:
        """
        Commit a decision to GAM.
        
        Args:
            content: The decision made
            reasoning: Why this decision was made
            confidence: Confidence level
            tags: Optional tags
        
        Returns:
            Memory ID if successful
        """
        if reasoning:
            full_content = f"{content}\n\n**Reasoning:** {reasoning}"
        else:
            full_content = content
        
        return self._commit(
            content=full_content,
            title="Decision",
            source="inferred",
            confidence=confidence,
            tags=tags or ["decision"],
        )
    
    def user_input(
        self,
        content: str,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Commit user-provided input.
        
        Args:
            content: What the user said/provided
            user_id: Optional user identifier
        
        Returns:
            Memory ID if successful
        """
        tags = ["user-input"]
        if user_id:
            tags.append(f"user:{user_id}")
        
        return self._commit(
            content=content,
            source="user",
            confidence="high",
            tags=tags,
        )
    
    def _commit(
        self,
        content: str,
        title: Optional[str] = None,
        source: str = "observation",
        confidence: str = "medium",
        classification: str = "private",
        tags: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Internal commit implementation."""
        trace_ctx = self.get_trace_context()
        
        if self._repo:
            return self._commit_direct(
                content=content,
                title=title,
                source=source,
                confidence=confidence,
                classification=classification,
                tags=tags,
                trace_context=trace_ctx,
            )
        elif self.api_url:
            return self._commit_http(
                content=content,
                title=title,
                source=source,
                confidence=confidence,
                classification=classification,
                tags=tags,
                trace_context=trace_ctx,
            )
        else:
            logger.warning("No GAM repo or API configured, skipping commit")
            return None
    
    def _commit_direct(
        self,
        content: str,
        title: Optional[str],
        source: str,
        confidence: str,
        classification: str,
        tags: Optional[list[str]],
        trace_context: TraceContext,
    ) -> Optional[str]:
        """Commit directly to repo."""
        try:
            from .core import MemoryMetadata
            
            metadata = MemoryMetadata(
                source=source,
                confidence=confidence,
                classification=classification,
                tags=tags or [],
            )
            
            memory = self._repo.remember(
                content=content,
                title=title,
                metadata=metadata,
                require_signature=False,
                trace_context=trace_context.to_dict() if trace_context.is_valid else None,
            )
            
            logger.info(f"GAM commit: {memory.id} (trace: {trace_context.trace_id})")
            return memory.id
            
        except Exception as e:
            logger.error(f"GAM direct commit failed: {e}")
            return None
    
    def _commit_http(
        self,
        content: str,
        title: Optional[str],
        source: str,
        confidence: str,
        classification: str,
        tags: Optional[list[str]],
        trace_context: TraceContext,
    ) -> Optional[str]:
        """Commit via HTTP API."""
        try:
            import httpx
            
            payload = {
                "content": content,
                "source": source,
                "confidence": confidence,
                "classification": classification,
                "tags": tags or [],
            }
            
            if title:
                payload["title"] = title
            
            headers = {}
            if trace_context.trace_id:
                headers["X-Trace-ID"] = trace_context.trace_id
            if trace_context.span_id:
                headers["X-Span-ID"] = trace_context.span_id
            
            response = httpx.post(
                f"{self.api_url}/remember",
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            memory_id = data.get("id")
            logger.info(f"GAM HTTP commit: {memory_id} (trace: {trace_context.trace_id})")
            return memory_id
            
        except Exception as e:
            logger.error(f"GAM HTTP commit failed: {e}")
            return None


def observe(gam: GAMAutoCommit, title: str, confidence: str = "medium"):
    """
    Decorator to auto-commit function results as observations.
    
    Usage:
        @observe(gam, "Analyzed intent")
        def analyze(message: str) -> dict:
            return {"intent": "greeting"}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            
            # Convert result to string for commit
            if isinstance(result, dict):
                import json
                content = f"```json\n{json.dumps(result, indent=2)}\n```"
            else:
                content = str(result)
            
            gam.observe(content, title=title, confidence=confidence)
            return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            if isinstance(result, dict):
                import json
                content = f"```json\n{json.dumps(result, indent=2)}\n```"
            else:
                content = str(result)
            
            gam.observe(content, title=title, confidence=confidence)
            return result
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    
    return decorator


def decision(gam: GAMAutoCommit, title: str = "Decision"):
    """
    Decorator to auto-commit function results as decisions.
    
    Usage:
        @decision(gam, "Route selection")
        def select_route(options: list) -> str:
            return "route_a"
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            gam.decision(str(result), confidence="high", tags=[title.lower().replace(" ", "-")])
            return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            gam.decision(str(result), confidence="high", tags=[title.lower().replace(" ", "-")])
            return result
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    
    return decorator


# Convenience singleton for simple usage
_default_gam: Optional[GAMAutoCommit] = None


def init_autocommit(
    repo_path: Optional[str] = None,
    api_url: Optional[str] = None,
) -> GAMAutoCommit:
    """
    Initialize the default GAM auto-commit instance.
    
    Checks env vars if no arguments provided:
    - GAM_REPO_PATH: Path to GAM repository
    - GAM_API_URL: GAM HTTP API URL
    """
    global _default_gam
    
    repo_path = repo_path or os.environ.get("GAM_REPO_PATH")
    api_url = api_url or os.environ.get("GAM_API_URL")
    
    _default_gam = GAMAutoCommit(repo_path=repo_path, api_url=api_url)
    return _default_gam


def get_autocommit() -> Optional[GAMAutoCommit]:
    """Get the default GAM auto-commit instance."""
    return _default_gam
