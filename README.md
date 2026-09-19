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
  these compute a recommended value for each of 17 tunable parameters,
  grouped into Memory / Connections / Query / WAL / Durability / Logging /
  Autovacuum — a set cross-checked against several independent PostgreSQL
  tuning guides (PGTune, the PostgreSQL Wiki, Percona, Mydbops) rather
  than picked arbitrarily.
- Each parameter is shown against PostgreSQL's real stock default (not a
  live "current" value — pg4all doesn't connect to a running database),
  with an advisory impact score, risk level, and whether changing it
  needs a restart (cross-checked against real parameter contexts, not
  just editorial). Override any of them (most are sliders; `synchronous_commit`
  is an on/off choice) before building. `synchronous_commit` is never
  recommended `off` by default — trading away commit durability is left
  as a deliberate, informed choice for the operator.
- Check the tuned set *as a whole* against the tier before building
  (`app/core/validation.py`). Each slider is individually in range, but the
  combination can still describe a server that won't hold up — on the small
  tier it takes about four drags to allocate more memory than the machine
  has, and PostgreSQL will start with that conf and get OOM-killed later
  under load. The rules cover the worst-case memory budget
  (`shared_buffers + wal_buffers + max_connections × work_mem +
  autovacuum_max_workers × maintenance_work_mem` against the tier's RAM),
  `shared_buffers` as a share of RAM, `effective_cache_size` against both
  `shared_buffers` and RAM, parallel workers against the worker pool, high
  `max_connections` with no pooler selected, and `synchronous_commit = off`.
  Findings update live as you drag. An error-level finding stops the build
  and asks you to confirm; warnings never block. The presets themselves do
  warn in two places, by design — the data-warehouse profile runs close to
  the memory line because its `work_mem` formula is meant to, and the OLTP
  profile's 300 connections are exactly what the pooler advice is for.
- Choose the host ports the generated stack publishes (`app/core/ports.py`).
  Every published port used to be hardcoded, which works right up until the
  host already has something on 5432 — the one port a machine doing
  PostgreSQL work is most likely to have taken. Only the host side is
  configurable: container ports are fixed properties of the images, and the
  sidecars reach Postgres over the compose network on 5432 regardless. A
  port that's out of range or claimed twice in the same stack blocks the
  build; one already held by a running container is only a warning, since
  the image builds fine and you may be about to stop whatever holds it.
  pgAdmin's port can move but its `127.0.0.1` bind cannot — putting the
  admin UI on the network should take more than editing a number.
- Build the resulting Docker image on demand, with your final values.
- Smoke-test the image right after it builds (`app/builder/smoke_test.py`).
  A successful `docker build` only proves the image assembled; PostgreSQL
  rounds, clamps, and ignores settings at startup without complaining, so
  the only way to know a conf did what it says is to ask a running server.
  The test boots the image on a throwaway volume with no published ports,
  waits for `pg_isready`, reads `pg_settings` back over the unix socket via
  `docker exec`, and compares every tuned parameter against what you asked
  for — then removes the container and its volume in a `finally`, on every
  path. A failure never invalidates the build: the image is on the daemon
  either way, and you decide what to do about it.
- Optionally include companion services alongside the build (`app/core/services.py`):
  PgBouncer, a Prometheus `postgres_exporter`, and pgAdmin, each generated as
  a sidecar container in a per-build `docker-compose.yml`; and pgBackRest,
  installed as a package inside the Postgres image itself (it needs direct
  access to the data directory, unlike the others, so it doesn't fit the
  sidecar model) with a generated single-node stanza config. None of these
  are orchestrated further — you still run `docker compose up`,
  `pgbackrest backup`, etc. yourself. pgAdmin is bound to `127.0.0.1` only
  (never published to the network) — reach it over an SSH tunnel to the
  host, not directly; that also covers "not in cleartext over the network"
  without pgAdmin needing to terminate TLS itself.

No database, no accounts. The one thing pg4all *does* persist is the
generated superuser credential for each build (see below) — everything
else is stateless, form-driven, for a single trusted operator.

Each build also generates a random password for the stock `postgres`
superuser and stores it encrypted on disk, keyed by build id
(`app/core/credentials.py`, `app/builder/credential_store.py`). The
password is never baked into the image — it's handed to you on the result
page (and again anytime at `/credentials/<build_id>`) to pass as
`POSTGRES_PASSWORD` when you `docker run` the image, the same mechanism the
official postgres image already uses. The encryption key
(`secrets/master.key`) is generated locally on first use and lives next to
the ciphertext (`credentials/`) — this protects against casual exposure
(backups, accidental commits, screenshots), not a full compromise of the
host the console runs on. Both directories are gitignored.

## Architecture

- FastAPI + Jinja2, server-rendered HTML, one small vanilla-JS file
  (`app/static/tuner.js`) for live slider feedback — no JS framework.
  The live configuration check works the same way round: `tuner.js` POSTs
  the form to `/validate` and drops in the HTML fragment that comes back,
  rather than reimplementing the thresholds in JavaScript, so there is one
  copy of each rule and it's the one `/build` enforces.
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
`PG4ALL_PORT`). It also bind-mounts `build_output/`, `secrets/`, and
`credentials/` to the host, so re-running it replaces the previous
container without losing the credential store or any per-build
`docker-compose.yml` generated for companion services — those live at a
real host path (`build_output/<build_id>/`) the operator can `cd` into.

`./scripts/run.sh` is a thin wrapper around the same command, if you'd
rather not type `PG4ALL_PORT=... docker compose up --build -d` directly.

## Tests

```bash
.venv/bin/pytest            # unit tests — hermetic, about a second
.venv/bin/pytest -m docker  # integration — builds images, runs containers
```

The `docker`-marked tests in `tests/test_smoke_test.py` are excluded from
the default run because they build real images and boot real containers.
They're the only place the whole chain is exercised end to end, and that
is where the interesting failures turn out to live: they exist because
every image pg4all built before them was unreachable on a published port
(see `FIXED_SETTINGS` in `app/core/conf_generator.py`), and no unit test
in this project could have seen it.

## Deliberately deferred (not missing — scoped out for now)

- Native OS packages (target families: Debian, RHEL) for non-container use.
- A second Docker base-image lineage (Red Hat UBI-based).
- Auth/multi-tenancy on the console.
- Anything beyond small/medium/large hardware presets.
- Patroni/HA clustering: it replaces how Postgres itself is started (a
  distributed consensus store, multi-node topology, dynamic config, leader
  election) rather than sitting beside a single static build, which is a
  different architecture from pg4all's "one image, one container" model
  today. Considered and explicitly deferred, not overlooked.
