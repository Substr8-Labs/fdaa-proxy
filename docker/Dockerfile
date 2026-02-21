# FDAA Proxy - Governed Gateway
FROM python:3.11-slim

WORKDIR /app

# Copy everything needed for install
COPY pyproject.toml README.md ./
COPY fdaa_proxy/ fdaa_proxy/

# Install with OTEL support
RUN pip install --no-cache-dir ".[otel]"

# Create data directory for audit logs
RUN mkdir -p /data

# Expose ports
EXPOSE 8766 8800

# Health check for OpenClaw proxy mode
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.connect(('localhost',8800)); s.close()" || exit 1

# Default: OpenClaw proxy mode
CMD ["fdaa-proxy", "openclaw", "start", "--host", "0.0.0.0", "--port", "8800"]
