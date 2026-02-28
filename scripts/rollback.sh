#!/bin/bash
# Rollback PRODUCTION to a previous version
# Usage: ./rollback.sh [rollback-tag]
#
# Lists available rollback tags if no argument provided

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")/docker"
LOG_FILE="$SCRIPT_DIR/../DEPLOY_LOG.md"

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
DEPLOYER="${USER:-unknown}"

if [ $# -eq 0 ]; then
    echo "Available rollback tags:"
    echo ""
    docker images --format "{{.Repository}}:{{.Tag}}" | grep "rollback-" | sort -u || echo "  No rollback tags found"
    echo ""
    echo "Usage: ./rollback.sh rollback-YYYYMMDDHHMMSS"
    exit 0
fi

ROLLBACK_TAG="$1"

echo "╔════════════════════════════════════════════╗"
echo "║          ROLLING BACK PRODUCTION           ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Rolling back to: $ROLLBACK_TAG"
echo "Time: $TIMESTAMP"
echo ""

# Verify rollback images exist
if ! docker image inspect "fdaa-proxy:$ROLLBACK_TAG" > /dev/null 2>&1; then
    echo "❌ Rollback image fdaa-proxy:$ROLLBACK_TAG not found"
    exit 1
fi

read -p "⚠️  This will rollback PRODUCTION. Continue? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "🔄 Restoring images..."
docker tag "fdaa-proxy:$ROLLBACK_TAG" fdaa-proxy:latest

echo "🚀 Redeploying production stack..."
docker stack deploy -c "$DOCKER_DIR/towerhq-stack.yml" towerhq

echo "⏳ Waiting for services (30s)..."
sleep 30

echo "🏥 Verifying health..."
if curl -sf "http://localhost:3100/health" > /dev/null 2>&1; then
    echo "  ✅ Bridge healthy"
else
    echo "  ❌ Bridge unhealthy"
fi

if curl -sf "http://localhost:19000/health" > /dev/null 2>&1; then
    echo "  ✅ Gateway healthy"
else
    echo "  ❌ Gateway unhealthy"
fi

echo ""
echo "✅ ROLLBACK COMPLETE"

# Log rollback
echo "" >> "$LOG_FILE"
echo "## $TIMESTAMP" >> "$LOG_FILE"
echo "- **Action:** ROLLBACK production" >> "$LOG_FILE"
echo "- **Deployer:** $DEPLOYER" >> "$LOG_FILE"
echo "- **Restored to:** $ROLLBACK_TAG" >> "$LOG_FILE"
echo "- **Status:** ✅ ROLLED BACK" >> "$LOG_FILE"

echo "📋 Logged to DEPLOY_LOG.md"
