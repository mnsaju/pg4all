#!/usr/bin/env bash
# Drive an ecommerce-shaped load at a pg4all-built Postgres, so the Grafana
# dashboard has something to show.
#
# An idle database makes every rate panel flat zero, which means "the panel
# is empty" and "the panel is broken" look identical — that is exactly how
# the PostgreSQL 17 checkpoint-metric bug stayed hidden. This is the tool
# for telling those apart: run it, watch the dashboard, see the panels move.
#
# Everything runs through `docker exec` against the Postgres container,
# which already has psql and pgbench, so the host needs neither.
#
# Usage:
#   scripts/loadtest/run.sh all              # setup, steady, spike, report
#   scripts/loadtest/run.sh setup|steady|spike|checkpoint|status|cleanup
#
# Environment:
#   PG_CONTAINER   container to drive (auto-detected if unset)
#   STEADY_SECONDS steady-phase duration (default 120)
#   SPIKE_SECONDS  spike-phase duration  (default 45)
#   SPIKE_FRACTION fraction of max_connections the spike opens (default 0.7)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STEADY_SECONDS="${STEADY_SECONDS:-120}"
SPIKE_SECONDS="${SPIKE_SECONDS:-45}"
SPIKE_FRACTION="${SPIKE_FRACTION:-0.7}"
NCUSTOMERS=5000
NPRODUCTS=1000

find_container() {
  if [ -n "${PG_CONTAINER:-}" ]; then echo "$PG_CONTAINER"; return; fi
  local found
  found=$(docker ps --filter ancestor=postgres --format '{{.Names}}' | head -1)
  if [ -z "$found" ]; then
    found=$(docker ps --format '{{.Names}}\t{{.Image}}' \
            | awk -F'\t' '$2 ~ /pg4all\/postgres/ {print $1; exit}')
  fi
  if [ -z "$found" ]; then
    echo "No pg4all Postgres container found. Set PG_CONTAINER explicitly." >&2
    echo "Running containers:" >&2
    docker ps --format '  {{.Names}}  ({{.Image}})' >&2
    exit 1
  fi
  echo "$found"
}

C="$(find_container)"
psql_q() { docker exec -u postgres -i "$C" psql -tAc "$1"; }

copy_scripts() {
  docker cp "$HERE/schema.sql"   "$C:/tmp/pg4all_schema.sql"   >/dev/null
  docker cp "$HERE/purchase.sql" "$C:/tmp/pg4all_purchase.sql" >/dev/null
  docker cp "$HERE/cleanup.sql"  "$C:/tmp/pg4all_cleanup.sql"  >/dev/null
}

bench() { # clients, seconds, extra args...
  local clients="$1" seconds="$2"; shift 2
  # One thread per 25 clients keeps a single pgbench thread from becoming
  # the bottleneck and under-reporting what the database could do.
  local jobs=$(( clients / 25 )); [ "$jobs" -lt 1 ] && jobs=1
  docker exec -u postgres "$C" pgbench \
    -n -f /tmp/pg4all_purchase.sql \
    -D "ncustomers=$NCUSTOMERS" -D "nproducts=$NPRODUCTS" \
    -c "$clients" -j "$jobs" -T "$seconds" -P 15 "$@" postgres
}

cmd_setup() {
  echo "==> Creating pg4all_loadtest schema in $C"
  copy_scripts
  docker exec -u postgres "$C" psql -q -f /tmp/pg4all_schema.sql
  echo "    $(psql_q 'SELECT count(*) FROM pg4all_loadtest.customers') customers, \
$(psql_q 'SELECT count(*) FROM pg4all_loadtest.products') products"
}

cmd_steady() {
  echo "==> Steady load: 8 clients for ${STEADY_SECONDS}s"
  echo "    Watch: transactions/sec finds a baseline, cache hit ratio settles."
  bench 8 "$STEADY_SECONDS"
}

cmd_spike() {
  local max_conn spike
  max_conn="$(psql_q 'SHOW max_connections')"
  spike="$(awk -v m="$max_conn" -v f="$SPIKE_FRACTION" 'BEGIN{printf "%d", m*f}')"
  [ "$spike" -lt 10 ] && spike=10
  echo "==> Spike: $spike clients for ${SPIKE_SECONDS}s (${SPIKE_FRACTION} of max_connections=$max_conn)"
  echo "    Watch: 'Connections by state' climbs toward its dashed threshold line."
  bench "$spike" "$SPIKE_SECONDS"
}

cmd_checkpoint() {
  # A short run triggers no checkpoint at all: they fire on checkpoint_timeout
  # (15 min by default here) or when WAL passes max_wal_size (gigabytes). So
  # the checkpoint panel stays flat unless something asks for one. This does,
  # explicitly — it is a genuine *requested* checkpoint, which is the series
  # the panel plots, but it is forced by this command rather than earned by
  # write pressure. Real requested checkpoints mean max_wal_size is too small.
  echo "==> Forcing a checkpoint (so the checkpoint panel has a data point)"
  echo "    Note: manually requested, not caused by WAL pressure."
  psql_q 'CHECKPOINT' >/dev/null
  echo "    requested checkpoints so far: $(psql_q 'SELECT num_requested FROM pg_stat_checkpointer' 2>/dev/null || psql_q 'SELECT checkpoints_req FROM pg_stat_bgwriter')"
}

cmd_status() {
  echo "==> $C"
  psql_q "SELECT '    orders: ' || count(*) FROM pg4all_loadtest.orders" 2>/dev/null \
    || echo "    pg4all_loadtest schema not present"
  echo "    connections: $(psql_q 'SELECT count(*) FROM pg_stat_activity')/$(psql_q 'SHOW max_connections')"
}

cmd_cleanup() {
  echo "==> Dropping pg4all_loadtest schema from $C"
  copy_scripts
  docker exec -u postgres "$C" psql -q -f /tmp/pg4all_cleanup.sql
  docker exec "$C" rm -f /tmp/pg4all_schema.sql /tmp/pg4all_purchase.sql /tmp/pg4all_cleanup.sql
  echo "    gone. Everything the load test created lived in that one schema."
}

case "${1:-all}" in
  setup)      cmd_setup ;;
  steady)     cmd_steady ;;
  spike)      cmd_spike ;;
  checkpoint) cmd_checkpoint ;;
  status)     cmd_status ;;
  cleanup)    cmd_cleanup ;;
  all)
    cmd_setup; echo; cmd_steady; echo; cmd_spike; echo; cmd_checkpoint; echo
    cmd_status
    echo
    echo "Data left in place so the dashboard keeps something to show."
    echo "Remove it with: scripts/loadtest/run.sh cleanup"
    ;;
  *) echo "Unknown command: $1" >&2; sed -n '7,20p' "${BASH_SOURCE[0]}" >&2; exit 1 ;;
esac
