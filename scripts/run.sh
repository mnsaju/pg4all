#!/usr/bin/env bash
# Build and run the console via docker-compose.yml (the source of truth
# for how it's built/run — this is just a convenience wrapper).
# The console is stateless (see README) so re-running this just replaces
# the previous container.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PORT="${PG4ALL_PORT:-8000}"

docker compose up --build -d

echo "pg4all console running at http://localhost:${PORT}"
echo "logs: docker compose logs -f"
