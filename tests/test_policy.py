"""Tests for the MCP Policy Engine."""

import pytest
from fdaa_proxy.mcp.policy import MCPPolicy, ToolPolicy, ToolCategory


def test_policy_from_dict():
    """Test creating policy from dictionary."""
    policy = MCPPolicy.from_dict({
        "server": "test-server",
        "mode": "allowlist",
        "tools": [
            {"name": "read_tool", "category": "read"},
            {"name": "write_tool", "category": "write", "requires_approval": True},
            {"name": "blocked_tool", "allowed": False},
        ]
    })
    
    assert policy.server_name == "test-server"
    assert policy.mode == "allowlist"
    assert len(policy.tool_policies) == 3


def test_tool_allowed():
    """Test checking if tool is allowed."""
    policy = MCPPolicy.from_dict({
        "server": "test",
        "mode": "allowlist",
        "tools": [
            {"name": "allowed_tool", "category": "read"},
            {"name": "blocked_tool", "allowed": False},
        ]
    })
    
    allowed, reason = policy.is_tool_allowed("allowed_tool")
    assert allowed is True
    
    allowed, reason = policy.is_tool_allowed("blocked_tool")
    assert allowed is False
    
    # Not in allowlist
    allowed, reason = policy.is_tool_allowed("unknown_tool")
    assert allowed is False


def test_approval_required():
    """Test checking approval requirements."""
    policy = MCPPolicy.from_dict({
        "server": "test",
        "mode": "allowlist",
        "write_requires_approval": True,
        "tools": [
            {"name": "read_tool", "category": "read"},
            {"name": "write_tool", "category": "write"},
            {"name": "explicit_approval", "category": "read", "requires_approval": True},
        ]
    })
    
    # Read tools don't need approval
    required, approvers = policy.requires_approval("read_tool")
    assert required is False
    
    # Write tools need approval (global setting)
    required, approvers = policy.requires_approval("write_tool")
    assert required is True
    
    # Explicit approval requirement
    required, approvers = policy.requires_approval("explicit_approval")
    assert required is True


def test_persona_access():
    """Test persona-based access control."""
    policy = MCPPolicy.from_dict({
        "server": "test",
        "mode": "allowlist",
        "tools": [
            {"name": "admin_tool", "category": "admin", "personas": ["admin", "root"]},
            {"name": "public_tool", "category": "read", "personas": ["*"]},
        ]
    })
    
    # Admin tool - admin allowed
    allowed, _ = policy.is_tool_allowed("admin_tool", persona="admin")
    assert allowed is True
    
    # Admin tool - regular user denied
    allowed, _ = policy.is_tool_allowed("admin_tool", persona="user")
    assert allowed is False
    
    # Public tool - anyone allowed
    allowed, _ = policy.is_tool_allowed("public_tool", persona="anyone")
    assert allowed is True


def test_predefined_policies():
    """Test predefined policy functions."""
    from fdaa_proxy.mcp.policy import github_readonly_policy, github_developer_policy
    
    readonly = github_readonly_policy()
    assert readonly.server_name == "@anthropic/mcp-server-github"
    assert "get_file_contents" in readonly.get_allowed_tool_names()
    assert "create_issue" not in readonly.get_allowed_tool_names()
    
    developer = github_developer_policy()
    assert "create_issue" in developer.get_allowed_tool_names()
