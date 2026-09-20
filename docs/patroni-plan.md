# Patroni support — plan

**Status:** planned, not implemented. Written 2026-09-20. Two scope
decisions are still open (see "Decisions to make first"); the phased plan
below assumes the recommended answers and notes where a different answer
changes it.

## Why this is a departure, not a bolt-on

Every other service pg4all offers sits *beside* Postgres (a sidecar
container, or a package in the image). Patroni is different: it replaces
how Postgres is started and configured, so it breaks several assumptions
the current "one tuned image, one container" model is built on. This is
why Patroni has been deliberately deferred until now, not overlooked.

| pg4all today | With Patroni |
|---|---|
| Static baked `postgresql.conf` | Patroni **owns** the conf and rewrites it; parameters live in Patroni's YAML / the DCS, not a file baked into the image |
| `initdb` args via `POSTGRES_INITDB_ARGS` on the official image | Patroni runs `initdb` itself, via its `bootstrap.initdb` config |
| One image, one container | A cluster: ≥3 Postgres+Patroni nodes **+ a DCS (etcd)** for leader election **+ a router (HAProxy)** so clients always reach the current leader |
| Official `postgres` image and its entrypoint | An image containing Patroni + a DCS client, with Patroni as the entrypoint |
| Smoke test: boot one container, check `pg_settings` | "Did it work" now means: a leader was elected, a replica is streaming, and failover promotes a replica |

**What still transfers — the reason an incremental step is possible:** the
tuning formulas, the parameter registry (`app/core/parameters.py`), and
validation (`app/core/validation.py`) all produce a format-agnostic *value
dict*. That dict can render into Patroni's config exactly as it renders
into `postgresql.conf` today. pg4all's core value — deciding the tuned
parameters and proving they applied — carries over; only the rendering
target and the run/verify topology change.

## The honesty line: dev cluster vs production HA

"Patroni support" can mean two very different things:

1. **Single-host dev/test cluster.** Three Postgres+Patroni nodes + etcd +
   HAProxy in one generated `docker-compose.yml`, on one host — the shape
   of Patroni's own demo. It is real Patroni (real leader election and
   failover you can trigger and watch) and it reuses pg4all's tuner. It is
   **not** production HA: one host, single-node etcd (no quorum), no fault
   isolation. Its purpose is learning, validating tuned config against a
   cluster, and exercising failover behaviour.

2. **Production multi-host HA.** Fault-isolated nodes across machines,
   etcd quorum across hosts, a floating VIP / cross-host client routing,
   per-node backups. This requires orchestrating *multiple hosts*, which
   pg4all's "build on the local Docker daemon" model does not do at all.
   This is the separate `pg-custom` control-plane effort's territory.

**Recommended:** implement (1) as the incremental step and keep (2)
explicitly deferred. (1) fits pg4all's "generate artifacts, the operator
runs them" model, reuses the tuner, and is testable on one host. (2) is a
different product and should not be smuggled in under pg4all's name.

## Decisions to make first

These two change the shape of Phases 1–3 and are recorded here unresolved.

1. **Scope.** Single-host dev cluster (recommended) · phase it (dev cluster
   now as the foundation, production HA later) · full production multi-host
   HA (a different product; `pg-custom` territory). The plan below assumes
   the single-host dev cluster.

2. **Image strategy.**
   - *Build Patroni into pg4all's own image* (recommended for coherence):
     `FROM postgres:<major>`, add Patroni + a DCS client + an entrypoint
     that runs Patroni. Reuses pg4all's extensions/initdb/tuning pipeline
     and its identity as an image builder; more Dockerfile work, and pg4all
     owns the HA-image maintenance.
   - *Use Spilo (Zalando's Postgres+Patroni image)*: faster to a working
     cluster and maintained, but bypasses pg4all's image customisation
     (extensions, initdb args, the two-tag strategy); tuning would be
     injected through Patroni/Spilo config instead of a pg4all-built image.

   The plan below assumes building into pg4all's own image; the Spilo
   variant mainly changes Phase 2 (reference an image instead of building
   one) and drops the Dockerfile work in Phase 3.

## Phased plan (single-host dev cluster, reusing the tuner)

### Phase 1 — Tuning → Patroni config (pure logic, no Docker)
- New `app/core/patroni_config.py`, pure and testable (same core/builder
  split as `conf_generator.py`). Input: the tuned value dict pg4all already
  computes. Output: Patroni's config structure —
  - cluster-wide parameters → `bootstrap.dcs.postgresql.parameters`
    (respecting which are reloadable vs restart-required),
  - `initdb` args (from `app/core/initdb.py`) → `bootstrap.initdb`,
  - `shared_preload_libraries` / extension settings carried over.
- Reuse `parameters.py` and `validation.py` unchanged — the values are
  format-agnostic.
- Tests: the same tuned values that render a known `postgresql.conf` render
  into valid, equivalent Patroni YAML.

### Phase 2 — Cluster compose generation (single host)
- New generator (e.g. `app/builder/patroni_compose_gen.py`) emitting:
  - N Postgres+Patroni node services (default 3), each with its own named
    volume and node-specific Patroni config,
  - one etcd service (single node — the DCS),
  - one HAProxy service routing `5432` → leader and `5433` → replicas,
    health-checked against Patroni's REST API (`/primary`, `/replica`).
- Credentials (`app/core/credentials.py`, `credential_store.py`): extend
  from one superuser password to superuser + **replication user** + Patroni
  REST API credentials.
- Image: per the image-strategy decision — build a Patroni-capable image
  from `dockerfile_gen.py`, or reference Spilo.

### Phase 3 — Build pipeline + UI
- A build **mode** in the tuner: single container (today) vs Patroni
  cluster, plus a node-count selector (default 3).
- Wire into the `/build` route and the background build in `app/main.py`;
  write the compose file, per-node Patroni YAML, and `haproxy.cfg` into
  `build_output/<build_id>/`, alongside the existing artifacts.
- If building the image ourselves: extend `dockerfile_gen.py` to add
  Patroni + DCS client + entrypoint for the cluster mode.

### Phase 4 — Cluster smoke test
- Extend the smoke test (`app/builder/smoke_test.py`) for cluster mode:
  bring the stack up, wait for a leader, assert one primary + N−1 streaming
  replicas, assert the tuned parameters applied on the leader (reuse the
  existing `pg_settings` check via the HAProxy leader port), and optionally
  trigger a failover (stop the leader, assert a replica is promoted). This
  is the HA analogue of today's "did the requested settings actually take
  effect" proof — and, as with the current smoke test, the place the real
  failures will surface.

### Phase 5 — Docs
- Document the cluster mode in the README: what it is, the single-host
  dev-cluster caveat, how to run it, and the failover demo.

## Explicitly deferred (still `pg-custom` territory)
- Production multi-host HA (fault-isolated nodes across machines).
- etcd quorum across hosts.
- Floating VIP / cross-host client routing.
- Cluster-integrated backups / PITR (pgBackRest per node).
- Rolling config changes applied across the cluster.
- Any multi-host control plane orchestrating more than one machine.
