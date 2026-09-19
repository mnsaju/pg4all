#!/usr/bin/env bash
# Build and run the console via docker-compose.yml (the source of truth
# for how it's built/run — this is just a convenience wrapper).
# The console is stateless (see README) so re-running this just replaces
# the previous container.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PORT="${PG4ALL_PORT:-8000}"
BIND="${PG4ALL_BIND:-127.0.0.1}"

docker compose up --build -d

echo "pg4all console running on ${BIND}:${PORT}"
if [ "${BIND}" = "127.0.0.1" ]; then
  echo
  echo "Bound to loopback only. From this host: http://localhost:${PORT}"
  echo "From anywhere else, tunnel to it — the console has no auth and"
  echo "host-root-equivalent access via the Docker socket:"
  echo "  ssh -L ${PORT}:127.0.0.1:${PORT} <host>"
else
  echo
  echo "WARNING: bound to ${BIND}, not loopback. The console has no"
  echo "authentication, serves every build's superuser password in"
  echo "cleartext at /credentials/<id>, and holds the host's Docker"
  echo "socket. Anyone who can reach ${BIND}:${PORT} has all of that."
fi
echo
echo "logs: docker compose logs -f"
