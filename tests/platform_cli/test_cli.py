"""Tests for substr8 harness CLI."""
import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

# Only test imports and basic CLI structure
# Integration tests require harness-core and services to be running


def test_harness_cli_imports():
    """Test that harness CLI module imports correctly."""
    from substr8.harness.cli import harness
    assert harness is not None
    assert harness.name == "harness"


def test_memory_cli_imports():
    """Test that memory CLI module imports correctly."""
    from substr8.memory.cli import memory
    assert memory is not None
    assert memory.name == "memory"


def test_threadhq_cli_imports():
    """Test that threadhq CLI module imports correctly."""
    from substr8.threadhq.cli import threadhq
    assert threadhq is not None
    assert threadhq.name == "threadhq"


def test_platform_v2_cli_imports():
    """Test that platform v2 CLI module imports correctly."""
    from substr8.platform_v2.cli import platform_v2
    assert platform_v2 is not None


def test_harness_validate_missing_path():
    """Test that harness validate fails gracefully for missing path."""
    from substr8.harness.cli import harness
    runner = CliRunner()
    result = runner.invoke(harness, ["validate", "/nonexistent/path"])
    assert result.exit_code != 0


def test_harness_inspect_missing_path():
    """Test that harness inspect fails gracefully for missing path."""
    from substr8.harness.cli import harness
    runner = CliRunner()
    result = runner.invoke(harness, ["inspect", "/nonexistent/path"])
    assert result.exit_code != 0


def test_memory_status_no_service():
    """Test that memory status fails gracefully when no service is running."""
    from substr8.memory.cli import memory
    runner = CliRunner()
    # Use a non-existent URL to ensure failure
    result = runner.invoke(memory, ["status"], env={"MEMORY_PLANE_URL": "http://localhost:19999", "GAM_SERVICE_URL": "http://localhost:19998"})
    assert result.exit_code != 0


def test_threadhq_status_no_service():
    """Test that threadhq status fails gracefully when no service is running."""
    from substr8.threadhq.cli import threadhq
    runner = CliRunner()
    result = runner.invoke(threadhq, ["status"], env={"THREADHQ_API_URL": "http://localhost:19999"})
    assert result.exit_code != 0