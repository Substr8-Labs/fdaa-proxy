# FDAA Proxy - Governed MCP Gateway
FROM python:3.11-slim

# Install Node.js for MCP server spawning (npx)
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application
COPY fdaa_proxy/ fdaa_proxy/

# Create data directory for audit logs
RUN mkdir -p /data

# Default config location
ENV FDAA_CONFIG=/app/config/gateway.yaml

# Expose port
EXPOSE 8766

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8766/health || exit 1

# Run server
CMD ["fdaa-proxy", "start", "-c", "/app/config/gateway.yaml"]
