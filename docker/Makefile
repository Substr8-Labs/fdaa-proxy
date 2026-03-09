# Substr8 Gateway Stack
# Shortcuts for common operations

.PHONY: up down logs status build clean

# Start gateway stack (detached)
up:
	docker compose up -d --build

# Stop gateway stack
down:
	docker compose down

# View logs (follow)
logs:
	docker compose logs -f

# Check status
status:
	docker compose ps

# Build images only
build:
	docker compose build

# Clean up (remove volumes too)
clean:
	docker compose down -v
	docker image prune -f

# Rebuild and restart
restart: down up
