#!/bin/bash
# Test FDAA Agent Registry API
# Usage: ./scripts/test-registry-api.sh [BASE_URL]

BASE_URL="${1:-http://localhost:8090}"

echo "=============================================="
echo "FDAA Agent Registry API Test"
echo "Base URL: $BASE_URL"
echo "=============================================="
echo

# Health check
echo "1. Health check..."
curl -s "$BASE_URL/health" | jq .
echo

# Root info
echo "2. Service info..."
curl -s "$BASE_URL/" | jq .
echo

# Create agent
echo "3. Creating agent 'val'..."
curl -s -X POST "$BASE_URL/v1/agents" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "val",
    "name": "Val",
    "description": "CFO of Control Tower",
    "files": [
      {
        "filename": "SOUL.md",
        "content": "# Val\n\nYou are Val, CFO of Control Tower.\n\nPersonality: Analytical, strategic, calm."
      },
      {
        "filename": "IDENTITY.md",
        "content": "# Identity\n\n- Name: Val\n- Role: CFO\n- Emoji: 📊"
      }
    ],
    "created_by": "test-script"
  }' | jq .
echo

# List agents
echo "4. Listing agents..."
curl -s "$BASE_URL/v1/agents" | jq .
echo

# Get agent
echo "5. Getting agent 'val'..."
curl -s "$BASE_URL/v1/agents/val" | jq .
echo

# Get system prompt
echo "6. Getting system prompt..."
curl -s "$BASE_URL/v1/agents/val/prompt" | jq .
echo

# Update agent
echo "7. Updating agent..."
curl -s -X PUT "$BASE_URL/v1/agents/val" \
  -H "Content-Type: application/json" \
  -d '{
    "files": [
      {
        "filename": "SOUL.md",
        "content": "# Val\n\nYou are Val, CFO of Control Tower.\n\nPersonality: Analytical, strategic, calm.\n\nVoice: Measured and precise."
      },
      {
        "filename": "IDENTITY.md",
        "content": "# Identity\n\n- Name: Val\n- Role: CFO\n- Emoji: 📊"
      }
    ],
    "commit_message": "Added voice section"
  }' | jq .
echo

# List versions
echo "8. Listing versions..."
curl -s "$BASE_URL/v1/agents/val/versions" | jq .
echo

# Registry stats
echo "9. Registry stats..."
curl -s "$BASE_URL/v1/registry/stats" | jq .
echo

# Spawn agent (will fail without OpenClaw, but tests endpoint)
echo "10. Testing spawn endpoint..."
curl -s -X POST "$BASE_URL/v1/agents/val/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, Val!"
  }' | jq .
echo

# Cleanup - delete agent
echo "11. Cleaning up - deleting agent..."
curl -s -X DELETE "$BASE_URL/v1/agents/val" | jq .
echo

echo "=============================================="
echo "Test complete!"
echo "=============================================="
