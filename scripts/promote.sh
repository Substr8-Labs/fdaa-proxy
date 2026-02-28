#!/bin/bash
# Promote STAGING to PRODUCTION
# Usage: ./promote.sh
#
# This script:
# 1. Verifies staging is healthy
# 2. Tags staging images as production versions
# 3. Updates production stack
# 4. Verifies production health
# 5. Logs the promotion

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")/docker"
LOG_FILE="$SCRIPT_DIR/../DEPLOY_LOG.md"

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
DEPLOYER="${USER:-unknown}"

# Get current prod version for rollback tagging
CURRENT_VERSION=$(date +%Y%m%d%H%M%S)

echo "╔════════════════════════════════════════════╗"
echo "║     PROMOTING STAGING → PRODUCTION         ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Time: $TIMESTAMP"
echo "Rollback tag: v$CURRENT_VERSION"
echo ""

# Step 1: Verify staging is healthy
echo "🏥 Verifying staging health..."
STAGING_HEALTHY=true

check_staging() {
    local port=$1
    if ! curl -sf "http://localhost:$port/health" > /dev/null 2>&1; then
        if ! curl -sf "http://localhost:$port/" > /dev/null 2>&1; then
            return 1
        fi
    fi
    return 0
}

if ! check_staging 13100; then
    echo "  ❌ Staging Bridge not healthy"
    STAGING_HEALTHY=false
fi

if ! check_staging 19001; then
    echo "  ❌ Staging Gateway not healthy"  
    STAGING_HEALTHY=false
fi

if [ "$STAGING_HEALTHY" = false ]; then
    echo ""
    echo "🛑 ABORTING: Staging is not healthy. Fix staging first."
    exit 1
fi

echo "  ✅ Staging is healthy"
echo ""

# Step 2: Confirm promotion
read -p "⚠️  This will update PRODUCTION. Continue? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""

# Step 3: Tag current prod for rollback
echo "📦 Tagging current prod for rollback..."
docker tag fdaa-proxy:latest fdaa-proxy:rollback-$CURRENT_VERSION 2>/dev/null || true
docker tag towerhq/bridge:v17 towerhq/bridge:rollback-$CURRENT_VERSION 2>/dev/null || true
docker tag towerhq/gateway-shared:v9 towerhq/gateway-shared:rollback-$CURRENT_VERSION 2>/dev/null || true

# Step 4: Promote staging images to prod tags
echo "🏷️  Promoting staging images..."
docker tag fdaa-proxy:staging fdaa-proxy:latest
NEW_BRIDGE_VERSION="v$(docker inspect --format='{{.Created}}' towerhq/bridge:staging 2>/dev/null | cut -c1-10 | tr -d '-' || echo '18')"
NEW_GATEWAY_VERSION="v$(docker inspect --format='{{.Created}}' towerhq/gateway-shared:staging 2>/dev/null | cut -c1-10 | tr -d '-' || echo '10')"

# For now, keep existing version tags since we're using fixed versions
# In a real setup, we'd increment versions

# Step 5: Update production stack
echo "🚀 Updating production stack..."
docker stack deploy -c "$DOCKER_DIR/towerhq-stack.yml" towerhq

# Step 6: Wait and verify
echo "⏳ Waiting for services (30s)..."
sleep 30

echo "🏥 Verifying production health..."
PROD_HEALTHY=true

check_prod() {
    local name=$1
    local port=$2
    
    if curl -sf "http://localhost:$port/health" > /dev/null 2>&1 || curl -sf "http://localhost:$port/" > /dev/null 2>&1; then
        echo "  ✅ $name (:$port)"
    else
        echo "  ❌ $name (:$port) - FAILED"
        PROD_HEALTHY=false
    fi
}

check_prod "Bridge" 3100
check_prod "Gateway" 19000
check_prod "Verify" 8080

echo ""
if [ "$PROD_HEALTHY" = true ]; then
    echo "✅ PRODUCTION PROMOTION SUCCESSFUL"
    STATUS="✅ SUCCESS"
else
    echo "⚠️  PRODUCTION HAS ISSUES - Consider rollback"
    echo "    Rollback: docker tag fdaa-proxy:rollback-$CURRENT_VERSION fdaa-proxy:latest && docker stack deploy -c $DOCKER_DIR/towerhq-stack.yml towerhq"
    STATUS="⚠️ ISSUES"
fi

# Step 7: Log promotion
echo "" >> "$LOG_FILE"
echo "## $TIMESTAMP" >> "$LOG_FILE"
echo "- **Action:** PROMOTE staging → production" >> "$LOG_FILE"
echo "- **Deployer:** $DEPLOYER" >> "$LOG_FILE"
echo "- **Rollback tag:** rollback-$CURRENT_VERSION" >> "$LOG_FILE"
echo "- **Status:** $STATUS" >> "$LOG_FILE"

echo ""
echo "📋 Logged to DEPLOY_LOG.md"
echo ""
echo "PRODUCTION ENDPOINTS:"
echo "  Bridge:   http://localhost:3100"
echo "  Gateway:  http://localhost:19000"
echo "  Verify:   http://localhost:8080"
echo "  Jaeger:   http://localhost:16686"
