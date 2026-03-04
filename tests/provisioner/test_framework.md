# FDAA Provisioner Test Framework

## Test Environment
- **Stack:** towerhq-staging
- **Gateway:** localhost:19001
- **FDAA API:** localhost:18766

## Test Cases

### 1. Agent Creation Location
- [ ] Agent created in persistent path (not /tmp)
- [ ] Path is `~/.openclaw/agents/fdaa/<agent_id>/`
- [ ] Directory has correct permissions (700)
- [ ] Survives container restart

### 2. Workspace Isolation
- [ ] Agent workspace is separate from main
- [ ] Agent cannot read files outside workspace
- [ ] Agent cannot write files outside workspace
- [ ] `agentDir` and `workspace` point to isolated location

### 3. Memory Isolation (CRITICAL)
- [ ] Agent cannot access main's MEMORY.md
- [ ] Agent cannot access main's USER.md
- [ ] Agent cannot access main's TOOLS.md
- [ ] Agent cannot access ~/.openclaw/secrets/
- [ ] Agent cannot see other agents' workspaces

### 4. Provisioning Lifecycle
- [ ] `POST /agents/{id}/provision` creates agent
- [ ] Config passes `openclaw doctor` validation
- [ ] Agent appears in `agents.list[]`
- [ ] Agent can be spawned via `sessions_spawn`
- [ ] `DELETE /agents/{id}/provision` removes agent
- [ ] Cleanup removes all agent files

### 5. Config Validation
- [ ] Generated config matches OpenClaw schema
- [ ] No `systemPrompt` in `identity` (use agentDir)
- [ ] No custom fields outside schema
- [ ] `main` agent always preserved

### 6. Security Boundaries
- [ ] Agent cannot escalate to main's tools
- [ ] Agent cannot modify other agents
- [ ] Agent cannot access gateway config
- [ ] Audit trail for all provisioning actions

## Test Commands

```bash
# Health check
curl -s http://localhost:18766/health

# List agents
curl -s http://localhost:18766/v1/agents

# Provision test agent
curl -X POST http://localhost:18766/v1/agents/test-agent/provision

# Verify isolation (should fail)
docker exec <container> cat /workspace/main/MEMORY.md

# Deprovision
curl -X DELETE http://localhost:18766/v1/agents/test-agent/provision
```

## Pre-Production Checklist
- [ ] All test cases pass in staging
- [ ] Security review completed
- [ ] No access to main workspace
- [ ] Audit logging verified
- [ ] Documentation updated
