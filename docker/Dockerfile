# FDAA Proxy - Governed Gateway with Runtime Integrity Layer
FROM python:3.11-slim

WORKDIR /app

# Install curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy everything needed for install
COPY pyproject.toml README.md ./
COPY fdaa_proxy/ fdaa_proxy/

# Install the package
RUN pip install --no-cache-dir -e .

# Create data directory for audit logs
RUN mkdir -p /data

# Expose ports
# 8766 - MCP Proxy
# 8800 - OpenClaw Proxy  
# 18800 - Anthropic Proxy (CIA)
EXPOSE 8766 8800 18800

# No default healthcheck - set per-service based on mode

# Default: help (require explicit mode selection)
ENTRYPOINT ["fdaa-proxy"]
CMD ["--help"]
