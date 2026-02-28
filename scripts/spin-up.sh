#!/bin/bash
# Spin up FDAA infrastructure from scratch
# Usage: ./spin-up.sh [--staging-only | --prod-only | --all]
#
# This script:
# 1. Creates required networks
# 2. Generates ACC keys if missing
# 3. Creates Docker configs/secrets
# 4. Deploys stack(s)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")/docker"
SECRETS_DIR="/home/node/.openclaw/secrets"
ACC_KEYS_DIR="$SECRETS_DIR/acc-keys"

MODE="${1:---all}"

echo "╔════════════════════════════════════════════╗"
echo "║      FDAA INFRASTRUCTURE SPIN-UP           ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Mode: $MODE"
echo ""

# Step 1: Create networks
echo "🌐 Creating networks..."
docker network create --driver overlay --attachable towerhq-gateways 2>/dev/null && echo "  Created towerhq-gateways" || echo "  towerhq-gateways exists"
docker network create --driver overlay --attachable towerhq-staging 2>/dev/null && echo "  Created towerhq-staging" || echo "  towerhq-staging exists"

# Step 2: Generate ACC keys if missing
echo ""
echo "🔐 Checking ACC keys..."
mkdir -p "$ACC_KEYS_DIR"

if [ ! -f "$ACC_KEYS_DIR/private.key" ]; then
    echo "  Generating new ACC keypair..."
    # Generate 256-bit keys (32 bytes)
    openssl rand -out "$ACC_KEYS_DIR/private.key" 32
    chmod 600 "$ACC_KEYS_DIR/private.key"
    
    # Derive public key (in real ACC, this would be proper derivation)
    # For now, we use a simple hash
    openssl dgst -sha256 -binary "$ACC_KEYS_DIR/private.key" > "$ACC_KEYS_DIR/public.key"
    
    # Generate key ID
    echo "acc-$(date +%Y%m%d)-$(openssl rand -hex 4)" > "$ACC_KEYS_DIR/key_id.txt"
    
    echo "  ✅ Generated new keypair: $(cat $ACC_KEYS_DIR/key_id.txt)"
else
    echo "  ✅ ACC keys exist: $(cat $ACC_KEYS_DIR/key_id.txt 2>/dev/null || echo 'unknown')"
fi

# Step 3: Create Docker configs/secrets
echo ""
echo "📝 Creating Docker configs..."

# Check if config exists, create if not
if ! docker config inspect shared-gateway-auth > /dev/null 2>&1; then
    # Create a default auth config
    echo '{"profiles":[]}' | docker config create shared-gateway-auth - && echo "  Created shared-gateway-auth" || echo "  Failed to create config"
else
    echo "  shared-gateway-auth exists"
fi

# Step 4: Check required environment variables
echo ""
echo "🔑 Checking environment..."
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    if [ -f "$SECRETS_DIR/anthropic-api-key.txt" ]; then
        export ANTHROPIC_API_KEY=$(cat "$SECRETS_DIR/anthropic-api-key.txt")
        echo "  Loaded ANTHROPIC_API_KEY from secrets"
    else
        echo "  ⚠️  ANTHROPIC_API_KEY not set (gateway may not work)"
    fi
else
    echo "  ✅ ANTHROPIC_API_KEY is set"
fi

if [ -z "${BRIDGE_SECRET:-}" ]; then
    if [ -f "$SECRETS_DIR/bridge-secret.txt" ]; then
        export BRIDGE_SECRET=$(cat "$SECRETS_DIR/bridge-secret.txt")
        echo "  Loaded BRIDGE_SECRET from secrets"
    else
        export BRIDGE_SECRET=$(openssl rand -hex 32)
        echo "$BRIDGE_SECRET" > "$SECRETS_DIR/bridge-secret.txt"
        echo "  Generated new BRIDGE_SECRET"
    fi
else
    echo "  ✅ BRIDGE_SECRET is set"
fi

# Step 5: Deploy stacks
echo ""

if [ "$MODE" = "--staging-only" ] || [ "$MODE" = "--all" ]; then
    echo "🚀 Deploying STAGING stack..."
    docker stack deploy -c "$DOCKER_DIR/towerhq-staging-stack.yml" towerhq-staging
    echo "  ✅ Staging deployed"
fi

if [ "$MODE" = "--prod-only" ] || [ "$MODE" = "--all" ]; then
    echo "🚀 Deploying PRODUCTION stack..."
    docker stack deploy -c "$DOCKER_DIR/towerhq-stack.yml" towerhq
    echo "  ✅ Production deployed"
fi

# Step 6: Wait and check health
echo ""
echo "⏳ Waiting for services to start (45s)..."
sleep 45

echo ""
echo "🏥 Health Check:"
echo ""

if [ "$MODE" = "--staging-only" ] || [ "$MODE" = "--all" ]; then
    echo "STAGING:"
    curl -sf "http://localhost:13100/health" > /dev/null 2>&1 && echo "  ✅ Bridge :13100" || echo "  ❌ Bridge :13100"
    curl -sf "http://localhost:19001/health" > /dev/null 2>&1 && echo "  ✅ Gateway :19001" || echo "  ❌ Gateway :19001"
    curl -sf "http://localhost:18080/" > /dev/null 2>&1 && echo "  ✅ Verify :18080" || echo "  ❌ Verify :18080"
    echo ""
fi

if [ "$MODE" = "--prod-only" ] || [ "$MODE" = "--all" ]; then
    echo "PRODUCTION:"
    curl -sf "http://localhost:3100/health" > /dev/null 2>&1 && echo "  ✅ Bridge :3100" || echo "  ❌ Bridge :3100"
    curl -sf "http://localhost:19000/health" > /dev/null 2>&1 && echo "  ✅ Gateway :19000" || echo "  ❌ Gateway :19000"
    curl -sf "http://localhost:8080/" > /dev/null 2>&1 && echo "  ✅ Verify :8080" || echo "  ❌ Verify :8080"
    curl -sf "http://localhost:16686/" > /dev/null 2>&1 && echo "  ✅ Jaeger :16686" || echo "  ❌ Jaeger :16686"
fi

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║            SPIN-UP COMPLETE                ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "STAGING:     http://localhost:13100 (bridge)"
echo "PRODUCTION:  http://localhost:3100  (bridge)"
echo "TRACING:     http://localhost:16686 (jaeger)"
echo "VERIFY:      http://localhost:8080  (audit UI)"
echo ""
echo "Next steps:"
echo "  1. Test changes in STAGING first"
echo "  2. Run ./promote.sh to push to PRODUCTION"
echo "  3. Use ./rollback.sh if issues occur"
