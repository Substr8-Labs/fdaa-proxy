#!/bin/bash
# Deploy to STAGING environment
# Usage: ./deploy-staging.sh [image-tag]
#
# This script:
# 1. Builds/tags images as :staging
# 2. Deploys the staging stack
# 3. Waits for health checks
# 4. Logs the deployment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")/docker"
LOG_FILE="$SCRIPT_DIR/../DEPLOY_LOG.md"
STACK_NAME="towerhq-staging"

IMAGE_TAG="${1:-staging}"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
DEPLOYER="${USER:-unknown}"

echo "╔════════════════════════════════════════════╗"
echo "║       DEPLOYING TO STAGING                 ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Stack: $STACK_NAME"
echo "Tag: $IMAGE_TAG"
echo "Time: $TIMESTAMP"
echo ""

# Tag current images as staging
echo "📦 Tagging images..."
docker tag fdaa-proxy:latest fdaa-proxy:staging 2>/dev/null || echo "  fdaa-proxy:latest not found, using existing :staging"
docker tag towerhq/bridge:latest towerhq/bridge:staging 2>/dev/null || docker tag towerhq/bridge:v17 towerhq/bridge:staging 2>/dev/null || echo "  Using existing bridge:staging"
docker tag towerhq/gateway-shared:latest towerhq/gateway-shared:staging 2>/dev/null || docker tag towerhq/gateway-shared:v9 towerhq/gateway-shared:staging 2>/dev/null || echo "  Using existing gateway:staging"

# Create network if needed
echo "🌐 Ensuring network exists..."
docker network create --driver overlay --attachable towerhq-staging 2>/dev/null || true

# Deploy stack
echo "🚀 Deploying stack..."
docker stack deploy -c "$DOCKER_DIR/towerhq-staging-stack.yml" "$STACK_NAME"

# Wait for services to be ready
echo "⏳ Waiting for services (30s)..."
sleep 30

# Health check
echo "🏥 Running health checks..."
HEALTHY=true

check_health() {
    local name=$1
    local port=$2
    local path=${3:-/health}
    
    if curl -sf "http://localhost:$port$path" > /dev/null 2>&1; then
        echo "  ✅ $name (:$port)"
    else
        echo "  ❌ $name (:$port) - FAILED"
        HEALTHY=false
    fi
}

check_health "Bridge" 13100
check_health "Gateway" 19001 "/health"
check_health "Verify" 18080 "/"

echo ""
if [ "$HEALTHY" = true ]; then
    echo "✅ STAGING DEPLOYMENT SUCCESSFUL"
    STATUS="✅ SUCCESS"
else
    echo "⚠️  STAGING DEPLOYMENT HAS ISSUES"
    STATUS="⚠️ PARTIAL"
fi

# Log deployment
echo "" >> "$LOG_FILE"
echo "## $TIMESTAMP" >> "$LOG_FILE"
echo "- **Environment:** STAGING" >> "$LOG_FILE"
echo "- **Deployer:** $DEPLOYER" >> "$LOG_FILE"
echo "- **Tag:** $IMAGE_TAG" >> "$LOG_FILE"
echo "- **Status:** $STATUS" >> "$LOG_FILE"
echo "- **Services:** bridge, fdaa-proxy, gateway, verify" >> "$LOG_FILE"

echo ""
echo "📋 Logged to DEPLOY_LOG.md"
echo ""
echo "STAGING ENDPOINTS:"
echo "  Bridge:   http://localhost:13100"
echo "  Gateway:  http://localhost:19001"
echo "  Verify:   http://localhost:18080"
