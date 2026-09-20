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
- Or say how much data you have and how many queries run at once, and get
  a machine suggested (`app/core/sizing.py`). This is the one part of
  pg4all that cannot point at a derivation: PGTune's formulas take RAM and
  CPU, so nothing in them takes data volume, and the step from "500 GB" to
  "128 GB of RAM" runs through an estimate of how much data is actually hot
  that no tool can know from outside. So it shows its working — one line
  per number, each arguable on its own — and points at the measurement that
  would disprove it, which is the cache hit ratio panel on the dashboard it
  also generates. Write throughput is deliberately not modelled: it would
  inform `max_wal_size` and autovacuum, but that would stack a second guess
  on the first, and the checkpoint panel is the honest way to find out.
- Pick a workload profile (web / OLTP / data warehouse / mixed / desktop —
  PGTune's own categories) and the machine: three presets, or the vCPU,
  RAM and storage type you actually have. The presets were never a
  simplification of the formulas — those only read vCPU, RAM and storage
  and would have accepted anything — they were a restriction on the input,
  which meant a 12-core, 48 GB server had to pretend to be "large (8/32)"
  and got `shared_buffers` of 8 GB instead of 12. Together
  these compute a recommended value for each of 21 tunable parameters,
  grouped into Memory / Connections / Query / WAL / Durability / Logging /
  Autovacuum — a set cross-checked against several independent PostgreSQL
  tuning guides (PGTune, the PostgreSQL Wiki, Percona, Mydbops) rather
  than picked arbitrarily. That set is checked against PGTune's own output
  by a test, because it turned out not to be: pg4all tuned seventeen
  parameters and PGTune emitted seventeen, but five of PGTune's had been
  swapped for five of pg4all's own without anyone noticing the drop. The
  worst of the missing ones was `max_parallel_workers`, which defaults to 8
  and is the cluster-wide ceiling on parallel query workers — so a 32-core
  data warehouse was told to use 16 workers per Gather and could never be
  given more than 8, with `max_worker_processes` at 32 making it look fine.
  `huge_pages` is the one deliberate omission: PostgreSQL already defaults
  it to `try`.
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
- See the generated `postgresql.conf` as you tune, and download it — from
  the tuner or from any past build. The preview comes from the same code
  path `/build` uses, so it cannot show one thing and build another (a test
  asserts the two are byte-identical). This also makes the tuner useful on
  its own: most people run PostgreSQL from a package on a machine they
  already have, and want the conf rather than the image.
- Choose the options `initdb` fixes when the cluster is first created
  (`app/core/initdb.py`). These are `context=internal`: unlike everything
  in the tuner they cannot be corrected by editing a conf and restarting,
  so getting one wrong is a dump and reload. Data page checksums default
  to **on**, because PostgreSQL leaves them off through 17 and turns them
  on from 18 — so without this a pg4all 16 or 17 image had no corruption
  detection while an 18 one did, and nothing said so. WAL segment size and
  collation are also offered. Two version traps are handled here rather
  than left to whoever writes the flags: `--no-data-checksums` only exists
  from 18, so turning them off below that means emitting nothing; and
  `--locale=C` on its own silently resolves the encoding to `SQL_ASCII`,
  disabling encoding validation entirely, so C collation is always paired
  with an explicit `--encoding=UTF8`. The smoke test is told what to expect
  and checks it, since this is the only moment a mistake is still catchable.

  Page size is the one thing genuinely out of reach: `block_size` is
  compile-time (`./configure --with-blocksize`), so changing it would mean
  building PostgreSQL from source and giving up the official images,
  binary compatibility with standard tooling, and `pg_upgrade` between
  differently-built clusters — for gains that benchmark as marginal outside
  narrow analytics cases. Huge pages, transparent huge pages, filesystem
  and I/O scheduler all sit below the container and are the host's business,
  not pg4all's.
- Build the resulting Docker image on demand, with your final values. The
  build runs in the background and streams to a page you can watch
  (`app/builder/build_store.py`): submitting returns straight away with a
  build id, and the daemon's output appears as it arrives. It used to hold
  the HTTP request open through the whole build and the smoke test, which
  is a few seconds on warm layers and minutes of blank page on a cold one.
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
- Optionally include a monitoring stack (`app/core/monitoring.py`): Prometheus
  to scrape and store, Grafana to display, and one dashboard generated from
  *this build's own tuning values* — connections plotted against the
  `max_connections` it chose, checkpoints against its `max_wal_size`. Ticking
  Grafana pulls in Prometheus and the exporter automatically, since a dashboard
  with nothing scraping is empty. Nine panels, all built from metrics the
  exporter really publishes — verified against a running stack in
  `tests/test_monitoring_stack.py`, which pulls every metric name out of the
  generated dashboard and asks a live Prometheus whether it has data. That test
  earned itself immediately: PostgreSQL 17 moved the checkpoint counters from
  `pg_stat_bgwriter` to a new `pg_stat_checkpointer` view, so the flagship
  checkpoint panel was silently empty on 17 and 18. The dashboard now picks the
  metric names for the major it built, and the exporter runs with
  `--collector.stat_checkpointer` (off by default, inert on 16).
  Both UIs are loopback-bound like pgAdmin; Prometheus especially, since it has
  no authentication at all. Metrics live in named volumes and Prometheus keeps
  15 days or 2 GB, whichever comes first.
- Optionally include other companion services alongside the build (`app/core/services.py`):
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

Each build gets its own permanent image tag carrying its build id
(`pg4all/postgres:17-oltp-medium-2dc19e67`, see `app/core/image_tags.py`),
and the short series tag (`pg4all/postgres:17-oltp-medium`) follows the
newest build of that combination — the same split Docker has always used
for release tags and `latest`. Generated compose files and run
instructions name the build tag, so a build's stack keeps starting the
image that build produced and the smoke test actually checked.

That split exists because the short tag alone was silently destructive: it
names a *category* while a build is an *event*, so every rebuild of the
same version, workload and tier took the previous build's tag. On the
machine this was found on, 11 records shared 3 tags across four genuinely
different configurations — and a build that had selected
`pg_stat_statements` pointed at an image with neither the
`shared_preload_libraries` line nor the init script, so running its own
compose file started a database with the extension simply absent and no
error anywhere. Extra tags cost nothing: an identical build context
produces an identical image, so a second tag is another pointer to the
same layers.

Builds can be deleted from the Builds page. That removes
`build_output/<build_id>/` and the stored credential, and optionally the
Docker image — its own build tag, plus the series tag when that still
points at the same image. Records made before per-build tags exist keep
their image: a bare series tag no longer identifies the image that build
produced. The image removal is never forced: if a container is still running from it the daemon
refuses and that refusal is reported rather than overridden. A running
build can't be deleted at all — it would carry on writing into a directory
that no longer exists. Deletion is confirmed on its own page first, and
the credential is not recoverable afterwards, so a container still running
from that image is left with no record of its password.

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
  directory, then triggers the build. Also owns everything under
  `secrets/`: the build credential store and the console's own password
  and session key.
- A running build's state lives on disk, in the same
  `build_output/<build_id>/` directory as its Dockerfile — `status.json`
  and an append-only `build.log`. That directory is bind-mounted to the
  host, so a build can be followed with `tail -f` as well as in the
  browser, and it survives the console restarting. In-memory jobs would
  have been less code right up until a restart lost one mid-build while
  the daemon carried on regardless; on disk, a build orphaned that way is
  marked `interrupted` at startup instead of spinning forever. Note the
  image may well have finished building even then — the console just
  stopped watching.
- Authentication is a middleware, not a per-route dependency, so a route
  added later is protected because it exists rather than because someone
  remembered to decorate it. `tests/test_main.py` walks the app's route
  table and asserts every non-public route redirects an anonymous caller,
  so that stays true.
- The console itself runs as its own container on a VM and triggers builds
  on the **host's** Docker daemon (Docker-outside-of-Docker), by mounting
  the host's `/var/run/docker.sock` into the console container and talking
  to it via the `docker` Python SDK — no `docker` CLI binary needed inside
  the console image.

  **This means the console has host-root-equivalent access.** It also
  serves real secrets: `/builds` lists every build made and
  `/credentials/<build_id>` returns that build's Postgres superuser
  password in cleartext. Encrypting the credential store at rest protects
  it from backups, screenshots and accidental commits — not from the web
  UI handing it out.

  Two things guard it, and they're independent on purpose. It binds to
  `127.0.0.1` only and is reached over an SSH tunnel, exactly like
  pgAdmin — overriding that takes a deliberate `PG4ALL_BIND=0.0.0.0`. And
  every route requires a session (see below), so a shell user on the host
  or another container that can reach the port still gets nothing.

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
host's Docker socket mounted in, bound to `127.0.0.1:8000` — loopback
only, never the network (override the port with `PG4ALL_PORT`). Reach it
from another machine over an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 <host>   # then open http://localhost:8000
```

`PG4ALL_BIND=0.0.0.0` publishes it to the network instead. The login gate
still applies, but this is a host-root-equivalent console — keep the
tunnel unless you have a reason not to. It's spelled out in full rather
than left as a default so it can't happen by accident.

It also bind-mounts `build_output/`, `secrets/`, and
`credentials/` to the host, so re-running it replaces the previous
container without losing the credential store or any per-build
`docker-compose.yml` generated for companion services — those live at a
real host path (`build_output/<build_id>/`) the operator can `cd` into.

`./scripts/run.sh` is a thin wrapper around the same command, if you'd
rather not type `PG4ALL_PORT=... docker compose up --build -d` directly.
It prints the tunnel command, and warns loudly if you've overridden the
bind.

## Signing in

Every route except `/login` and `/static` requires a session.

On first run the console generates a password and prints it once, in a
banner in its own log:

```bash
docker compose logs pg4all-console
```

Only an scrypt hash of it is stored (`secrets/console-password.json`), so
that banner is the one time the password exists anywhere but in your
hands — save it then. To rotate it, delete that file and restart; a new
one is generated and printed.

Sessions are a signed cookie (`HttpOnly`, `SameSite=Lax`, 12 hours),
signed with a key in `secrets/console-session.key`. Deleting that key and
restarting invalidates every outstanding session. The cookie is not
`Secure`, because the console is served over plain HTTP on loopback and
the SSH tunnel is what provides transport confidentiality; setting it
would stop the cookie being sent at all.

There is one operator and no user system — no accounts, no roles, no
registration, no reset flow. A single-operator tool doesn't need a user
table; it needs unauthenticated requests to get nothing.

## Tests

```bash
.venv/bin/pytest              # unit tests — hermetic, about a second
.venv/bin/pytest -m docker    # integration — builds images, runs containers
.venv/bin/pytest -m loadtest  # drives real load at a generated stack
```

The `docker`-marked tests in `tests/test_smoke_test.py` are excluded from
the default run because they build real images and boot real containers.
They're the only place the whole chain is exercised end to end, and that
is where the interesting failures turn out to live: they exist because
every image pg4all built before them was unreachable on a published port
(see `FIXED_SETTINGS` in `app/core/conf_generator.py`), and no unit test
in this project could have seen it.

## Load testing the dashboard

An idle database makes every rate panel flat zero, so "the panel is empty"
and "the panel is broken" look identical — which is exactly how the
PostgreSQL 17 checkpoint-metric bug stayed hidden. `scripts/loadtest/`
drives an ecommerce-shaped workload so the dashboard has something to show:

```bash
scripts/loadtest/run.sh all      # setup, steady, spike, forced checkpoint
scripts/loadtest/run.sh cleanup  # remove everything it created
```

It uses `pgbench`, which is already in every image pg4all builds — no
extra container and no new dependency. One purchase is one transaction: a
price read, a stock update, an order insert, a line-item insert and a
customer update, so it generates commit volume, WAL and lock activity
rather than just SELECT load. The spike scales to 70% of *this build's*
`max_connections`, which is what drives the connections panel visibly up
toward its threshold line.

It is a development tool, not a product feature: pg4all builds and tunes
images, it does not offer load testing as a service.

Everything it creates lives in one `pg4all_loadtest` schema, so removal is
a single `DROP SCHEMA ... CASCADE` that cannot miss a table. Against a
throwaway stack that is academic — `docker compose down -v` takes the
volume anyway — but it matters the moment this is pointed at a build you
are keeping.

One honest caveat: the checkpoint panel cannot be driven by a short run.
Checkpoints fire on `checkpoint_timeout` (15 minutes) or when WAL passes
`max_wal_size` (gigabytes), so `run.sh` issues an explicit `CHECKPOINT` to
give the panel a data point and says so. That is a genuine *requested*
checkpoint, but it was forced rather than earned — in production, requested
checkpoints outpacing timed ones is the signal that `max_wal_size` is too
small.

`tests/test_loadtest.py` runs the same scripts and asserts each panel's
metric actually moved, directionally rather than to exact numbers —
throughput on a shared machine is not reproducible, and a test asserting
"2000 tps" fails for reasons that have nothing to do with pg4all.

## Third-party license notices

pg4all's deliverable bundles a lot of open-source software — the Debian
base and PostgreSQL inside the image it builds, plus the companion images
the generated compose file pulls (Grafana, Prometheus, the exporter, …).
Distributing that bundle carries every component's own license
obligations, so each successful build writes a `THIRD_PARTY_NOTICES.md`
into `build_output/<build_id>/`, downloadable from the build page.

There is no single "superset" license to generate: the components are an
aggregation of independently licensed programs, and the copyleft ones —
GPL, LGPL, and Grafana's AGPL — keep their own terms and cannot be
relicensed under a pg4all umbrella. So the file is an *inventory*, not a
license. It lists each component and its SPDX license, groups them by the
strength of the obligation (network/strong/weak copyleft, then
permissive), and declares the license of pg4all's own generated
Dockerfile/conf (`OUTPUT_LICENSE_DECLARATION` in `app/core/licensing.py`).

The built image is scanned in place with syft, run as a throwaway
container against the host daemon over the same socket the console already
mounts — so no tool is added to the console image; the syft image is
pulled on first use. The companion images are not scanned (that would mean
pulling every one over the network at build time); each is identified by
the primary project license recorded beside its pin in
`app/core/services.py`. The verbatim per-package license texts already
travel inside the image at `/usr/share/doc/*/copyright` (the Debian
convention), so the manifest points there rather than duplicating tens of
thousands of lines of license text.

If the scan can't run, the file is still written — one that says the image
scan was unavailable, rather than one that silently omits the image's
components. Compliance information that looks complete but isn't is the
failure worth avoiding.

This is an inventory to help meet notice obligations, not legal advice.
Two things warrant a human's confirmation before you rely on it: the
license declared for pg4all's own generated files, and — if you ship the
monitoring stack — that you are meeting Grafana's AGPL obligations.

## Deliberately deferred (not missing — scoped out for now)

- Native OS packages (target families: Debian, RHEL) for non-container use.
- A second Docker base-image lineage (Red Hat UBI-based).
- Multi-user accounts, roles, or an audit log on the console. It
  authenticates a single operator (see "Signing in"); it is not a
  multi-tenant system and isn't trying to become one.
- Rate limiting or lockout on the login form. scrypt's cost is the
  throttle, and the console isn't reachable from the network; a lockout on
  a single-operator tool is mostly a way to lock out the operator.
- Anything beyond small/medium/large hardware presets.
- A machine-readable SBOM (SPDX/CycloneDX) alongside the license notices,
  and in-console warnings when a build's bundle includes copyleft. The
  notices file already records the licenses (see "Third-party license
  notices"); emitting an SBOM and surfacing copyleft in the UI are the
  next phases, deferred until asked for. So is extracting each package's
  verbatim copyright text into the file rather than pointing at the copies
  inside the image. A license *policy/selection* resolver is out of scope
  entirely — that belongs to the separate pg-custom effort, not pg4all.
- Patroni/HA clustering: it replaces how Postgres itself is started (a
  distributed consensus store, multi-node topology, dynamic config, leader
  election) rather than sitting beside a single static build, which is a
  different architecture from pg4all's "one image, one container" model
  today. Considered and explicitly deferred, not overlooked.
