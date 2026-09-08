# pg4all

A small, incremental alternative to Pigsty. Where Pigsty is a full
Ansible-driven fleet platform, pg4all v1 is a single-page Config Tuner
console: pick a PostgreSQL version, workload profile, and hardware tier,
fine-tune individual parameters with sliders, and build a Docker image
with the resulting `postgresql.conf` baked in.

## v1 scope

- Official upstream `postgres` Docker images, Debian-slim only (no RHEL/UBI
  variant yet, no from-source build).
- Pick from the latest 3 supported PostgreSQL majors (`app/core/pg_versions.py`
  — hardcoded, update by hand when a new major/minor ships).
- Pick a workload profile (web / OLTP / data warehouse / mixed / desktop —
  PGTune's own categories) and a hardware tier (small / medium / large
  presets — not a precise sizing calculator; there's no data-volume/
  throughput input to justify more precision than that yet). Together
  these compute a recommended value for each of 15 tunable parameters,
  grouped into Memory / Connections / Query / WAL / Logging / Autovacuum.
- Each parameter is shown against PostgreSQL's real stock default (not a
  live "current" value — pg4all doesn't connect to a running database),
  with an advisory impact score, risk level, and whether changing it
  needs a restart (cross-checked against real parameter contexts, not
  just editorial). Override any of them with its slider before building.
- Build the resulting Docker image on demand, with your final values.

Nothing here is persisted — no database, no accounts. It's a stateless
form-driven tool for a single trusted operator.

## Architecture

- FastAPI + Jinja2, server-rendered HTML, one small vanilla-JS file
  (`app/static/tuner.js`) for live slider feedback — no JS framework.
- `app/core/` — pure, testable logic: version list, workload/hardware
  definitions, the conf generator, PostgreSQL's stock defaults
  (`pg_defaults.py`), and the tunable-parameter registry
  (`parameters.py`, which also groups everything for the UI). No
  FastAPI imports in here.
- `app/builder/` — writes the Dockerfile + conf to a build context
  directory, then triggers the build.
- The console itself runs as its own container on a VM and triggers builds
  on the **host's** Docker daemon (Docker-outside-of-Docker), by mounting
  the host's `/var/run/docker.sock` into the console container and talking
  to it via the `docker` Python SDK — no `docker` CLI binary needed inside
  the console image.

  **This means the console has host-root-equivalent access.** Do not
  expose it beyond a trusted operator until there's an actual auth story.

## Run locally (no Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The "Build" step needs a reachable Docker
daemon (`DOCKER_HOST`, or the default local socket).

## Run as a container, building on the host daemon

```bash
docker compose up --build -d
```

`docker-compose.yml` builds the console image and runs it with the
host's Docker socket mounted in, on port 8000 (override with
`PG4ALL_PORT`). Re-running it replaces the previous container — the
console is stateless, so nothing is lost.

`./scripts/run.sh` is a thin wrapper around the same command, if you'd
rather not type `PG4ALL_PORT=... docker compose up --build -d` directly.

## Tests

```bash
.venv/bin/pytest
```

## Deliberately deferred (not missing — scoped out for now)

- Native OS packages (target families: Debian, RHEL) for non-container use.
- A second Docker base-image lineage (Red Hat UBI-based).
- Auth/multi-tenancy on the console.
- Anything beyond small/medium/large hardware presets.
