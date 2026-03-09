"""
GAM API Module

FastAPI service for multi-tenant memory operations.
"""

from .main import app, run_server

__all__ = ["app", "run_server"]
