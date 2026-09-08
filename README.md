# pg4all

A small, incremental alternative to Pigsty. Where Pigsty is a full
Ansible-driven fleet platform, pg4all v1 is just a console that helps you
pick a PostgreSQL version and workload, generates a tuned
`postgresql.conf`, and builds a Docker image with it baked in.

## v1 scope

- Official upstream `postgres` Docker images, Debian-slim only (no RHEL/UBI
  variant yet, no from-source build).
- Pick from the latest 3 supported PostgreSQL majors (`app/core/pg_versions.py`
  — hardcoded, update by hand when a new major ships).
- Pick a workload type (web / OLTP / data warehouse / mixed / desktop —
  PGTune's own categories). This drives:
  - a recommended hardware tier (small / medium / large presets — not a
    precise sizing calculator; there's no data-volume/throughput input to
    justify more precision than that yet).
  - a generated `postgresql.conf`, once the hardware tier is confirmed,
    using sizing formulas ported from PGTune rather than re-derived.
- Build the resulting Docker image on demand.

Nothing here is persisted — no database, no accounts. It's a stateless
form-driven tool for a single trusted operator.

## Architecture

- FastAPI + Jinja2, server-rendered HTML forms, no JS framework.
- `app/core/` — pure, testable logic: version list, workload definitions,
  hardware tiers, and the conf generator. No FastAPI imports in here.
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
./scripts/run.sh
```

Builds the console image and runs it with the host's Docker socket
mounted in, on port 8000 (override with `PG4ALL_PORT`). Re-running it
replaces the previous container — the console is stateless, so nothing
is lost. Equivalent to:

```bash
docker build -f docker/console.Dockerfile -t pg4all-console .
docker run -d --name pg4all-console -p 8000:8000 -v /var/run/docker.sock:/var/run/docker.sock pg4all-console
```

## Tests

```bash
.venv/bin/pytest
```

## Deliberately deferred (not missing — scoped out for now)

- Native OS packages (target families: Debian, RHEL) for non-container use.
- A second Docker base-image lineage (Red Hat UBI-based).
- Auth/multi-tenancy on the console.
- Anything beyond small/medium/large hardware presets.
