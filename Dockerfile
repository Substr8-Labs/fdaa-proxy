FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /data

# Default environment
ENV FDAA_HOST=0.0.0.0
ENV FDAA_PORT=8766
ENV RIL_ENABLED=false

# Expose port
EXPOSE 8766

# Run the API server
CMD ["python", "-m", "fdaa_proxy.cli", "start", "--host", "0.0.0.0", "--port", "8766"]
