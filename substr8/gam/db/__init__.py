"""
GAM Database Module

Postgres + pgvector storage for multi-tenant memory.
"""

from .migrate import migrate

__all__ = ["migrate"]
