#!/usr/bin/env bash
# Build the console image and run it against the host's Docker daemon.
# The console is stateless (see README) so re-running this just replaces
# the previous container.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

IMAGE_NAME="pg4all-console"
CONTAINER_NAME="pg4all-console"
PORT="${PG4ALL_PORT:-8000}"

docker build -f docker/console.Dockerfile -t "$IMAGE_NAME" .

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -d \
  --name "$CONTAINER_NAME" \
  -p "${PORT}:8000" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  "$IMAGE_NAME"

echo "pg4all console running at http://localhost:${PORT}"
echo "logs: docker logs -f ${CONTAINER_NAME}"
