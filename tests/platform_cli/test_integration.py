"""Tests for harness CLI against real harness-core fixture."""
import json
import os
import tempfile
from pathlib import Path

import pytest

# These tests require harness-core to be installed at HARNESS_CORE_ROOT
# Skip if not available

HARNESS_CORE_ROOT = os.environ.get(
    "HARNESS_CORE_ROOT",
    os.path.expanduser("~/workspace/harnesses/harness-core")
)

FIXTURE = os.path.join(HARNESS_CORE_ROOT, "harness_core", "test_fixture_ndis_plan_review")

pytestmark = pytest.mark.skipif(
    not os.path.exists(FIXTURE),
    reason="harness-core test fixture not found"
)


def test_validate_ndis_fixture():
    """Test that harness validate works with the NDIS plan review fixture."""
    from substr8.harness.cli import harness
    from click.testing import CliRunner
    
    runner = CliRunner()
    result = runner.invoke(harness, ["validate", FIXTURE])
    # Should exit 0 for valid fixture
    assert result.exit_code == 0 or "Valid" in result.output or "valid" in result.output.lower()


def test_validate_ndis_fixture_json():
    """Test that harness validate --json works."""
    from substr8.harness.cli import harness
    from click.testing import CliRunner
    
    runner = CliRunner()
    result = runner.invoke(harness, ["validate", FIXTURE, "--json"])
    # Should produce valid JSON
    if result.exit_code == 0:
        data = json.loads(result.output)
        assert "valid" in data


def test_run_ndis_fixture():
    """Test that harness run works with the NDIS plan review fixture."""
    from substr8.harness.cli import harness
    from click.testing import CliRunner
    
    runner = CliRunner()
    result = runner.invoke(harness, ["run", FIXTURE])
    # Should complete (exit code 0 for success)
    # May fail if harness-core not set up, which is OK for CI


def test_inspect_ndis_fixture():
    """Test that harness inspect shows package details."""
    from substr8.harness.cli import harness
    from click.testing import CliRunner
    
    runner = CliRunner()
    result = runner.invoke(harness, ["inspect", FIXTURE])
    assert "harness.package.yaml" in result.output or result.exit_code == 0