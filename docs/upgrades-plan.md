# PostgreSQL upgrade helpers — plan

**Status:** planned, not implemented. Written 2026-09-20. Captures the
approach for helping users move a built image/cluster to a newer
PostgreSQL version. The guiding decision: minor and major upgrades are
different problems and get different treatment.

## The fundamental split: minor vs major

PostgreSQL's on-disk format is stable *within* a major version and
*changes* between majors. That single fact separates a trivial, safe
operation from a data-loss-capable one.

| | Minor (e.g. 17.2 → 17.5) | Major (e.g. 17 → 18) |
|---|---|---|
| On-disk format | Identical — storage format guaranteed stable within a major | Changes — the new binary cannot read the old data directory |
| Mechanism | Stop old binary, start new binary on the **same** PGDATA | `pg_dump`/restore, or `pg_upgrade`, or logical replication |
| Binaries needed | One (the new minor) | **Both** old and new majors at once |
| Risk | Low | High — data-loss territory if done naively |
| Downtime | Seconds | Minutes to hours (or complex to avoid) |

## Why pg4all is well placed to help

A *generic* upgrade script is blind to the cluster it's upgrading. pg4all
is not: it knows the build's major version, its extensions, its
initdb-time settings (checksums, collation, WAL segment size), its tuned
conf, and whether pgBackRest is baked in. So the plan is **per-build
generated helpers**, not one generic download-and-run script — the same
way pg4all already generates the Dockerfile/conf/compose for a specific
build. Much of the major-upgrade pre-flight then passes *by construction*
(the target image contains the right extension versions, the target
cluster is initialized with the matching checksum setting, the conf is
regenerated for the new major) rather than by hoping a check passes.

## Minor upgrades — automate, low risk

Within a major, upgrading is just: swap the image tag, recreate the
container against the same volume. No dump, no `pg_upgrade`. Generate an
`upgrade-minor.sh` per build that:

1. **Asserts same major** and refuses otherwise — the guardrail that keeps
   a minor script from being aimed at a major.
2. **Backs up / snapshots the volume** first (cheap insurance).
3. **Stops the old container, starts the new-minor image on the same
   PGDATA**, and verifies the server accepts connections and reports the
   expected version.
4. **Checks for a collation-version mismatch.** The one real minor-upgrade
   landmine is a glibc/ICU collation change in the base image silently
   corrupting text indexes; PostgreSQL 15+ records a collation version and
   warns on mismatch. The script surfaces this and tells the user to
   `REINDEX`. (pg4all pins its base image, so this only bites on a
   deliberate base-OS move — but the script should catch it, not assume.)

Verdict: worth doing, small, fits the "generate an artifact the operator
runs" model.

## Major upgrades — assisted, pre-flighted, backup-gated, approval-required

Do **not** ship a generic auto-upgrade script. Ship a per-build *assisted*
helper plus a runbook, where the authoritative compatibility work is
delegated to PostgreSQL's own **`pg_upgrade --check`** (a non-destructive
mode) and pg4all contributes what it uniquely knows. The helper never runs
a mutating step without a verified backup and explicit human approval.

### Pre-flight checks (all read-only, run and reported first)

1. **Image / upgrade compatibility.** Confirm source ≤ target and source ≥
   `pg_upgrade`'s floor (9.2); confirm both majors' binaries are present
   (the dual-version requirement — needs a dual-version image such as
   `tianon/docker-postgres-upgrade`, see "Existing tools" below, or the
   `pg_dump` path which needs only the new `pg_dump` + old server); then
   run `pg_upgrade --check` as the authoritative gate. `--check` needs the
   target cluster already `initdb`'d with matching settings, which is
   non-destructive to the old data.

2. **Extension compatibility, including version.** `pg_upgrade --check`
   already fails on a missing loadable library. The helper adds a version
   comparison: `pg_extension.extversion` on the source vs.
   `pg_available_extension_versions` on the target, confirming the target's
   default version is ≥ the installed one and that an upgrade path exists;
   it emits the needed `ALTER EXTENSION ... UPDATE` post-steps. **Limits:** a
   few extensions carry their own rules the helper can detect-and-guide but
   not fully automate — PostGIS (`postgis_extensions_upgrade()`),
   TimescaleDB (strict version matrix, often forbids `pg_upgrade`). Because
   pg4all builds the target image, it can guarantee matching-or-newer
   versions of *this build's* extensions.

3. **Checksum parity.** Read the source's `data_checksums` state
   (`SHOW data_checksums` / `pg_controldata`) and `initdb` the target to
   match, or `pg_upgrade` refuses with a checksum-version mismatch. Live
   concern: PostgreSQL 18 flipped the default to on, so a 17-checksums-off
   cluster → 18 must initialize the new cluster with `--no-data-checksums`.
   pg4all knows the source build's setting, so it pre-sets the target.

4. **Config check.** Not a literal match of the old `postgresql.conf` —
   that is the trap, since removed/renamed GUCs stop the new server
   starting. Instead **regenerate** the tuned conf for the *target* major
   (pg4all's version-aware generator) and **validate** by test-starting the
   target with it and confirming no unknown-GUC / out-of-range errors.

### Execution order (the human gate)

1. Run all pre-flight checks (1–4), read-only, and print a clear report.
2. **Verify a backup exists**, or take one (leaning on pgBackRest if the
   build has it). Non-negotiable.
3. **Require explicit human approval** before any mutating step — and a
   *separate, extra* opt-in for the destructive `pg_upgrade --link` mode
   (fast, but can leave the old cluster unusable if the new one fails).
   Default to the safe path where the old data is untouched: `--copy`, or
   `pg_dump`/restore for typical sizes.
4. Execute, then **verify**: server up, schema/row sanity check,
   `ALTER EXTENSION ... UPDATE`, `REINDEX` where flagged.

### What the helper cannot guarantee (state plainly to users)

- **Application-level compatibility.** `pg_upgrade`/`pg_dump` preserve
  schema and data, but changed defaults/behaviours across majors can affect
  the application. Only the user's own testing catches that.
- **Extension-specific quirks** for PostGIS / TimescaleDB / out-of-tree
  extensions — detect-and-guide, not fully automate.
- It is a friendly pre-report; **`pg_upgrade --check` is the real
  authority, and a verified backup is the real safety net.**

### Existing tools to build on (not reinvent)

Researched 2026-09-20. Open-source prior art the helper should wrap or lean
on rather than reimplement.

- **`tianon/docker-postgres-upgrade`** — the canonical dual-version Docker
  image for running `pg_upgrade` in a container, with tags in `OLD-to-NEW`
  form (e.g. `17-to-18`). This is the dual-binary image the major path
  needs; wrap it, or build pg4all's own dual-version image the same way,
  rather than assembling one from scratch. (Upstream bills it as a PoC to
  adapt, not a turnkey tool.)
- **`pgautoupgrade`** (`pgautoupgrade/docker-pgautoupgrade`) — the closest
  existing thing to single-container auto-upgrade, and an instructive
  *anti-pattern* for our defaults. A drop-in for the official image that,
  on startup, detects an older-major PGDATA and automatically runs
  `pg_upgrade --link`, then **removes the old cluster data** on success. It
  is fast and effortless, but `--link` mutates files in place and the old
  data is deleted with no backup gate and no approval — the project itself
  warns a prior backup is non-negotiable. This is exactly the aggression
  pg4all's assisted, backup-gated, approval-required design avoids. Worth
  offering at most as an explicit opt-in "fast/dev" mode, never the
  default. It's also the reference for the *detect-old-PGDATA-on-startup*
  mechanic.
- **`pgcopydb`** — a mature C tool that clones a database and follows
  changes (CDC) to migrate very large databases with almost-zero downtime.
  The tool to lean on for the logical / near-zero-downtime major path,
  instead of hand-rolling logical replication. **`pg_easy_replicate`** is a
  lighter blue/green orchestrator with the same idea (sub-minute downtime)
  but is Ruby, so treat it as a reference rather than a dependency.
- **Debian `pg_upgradecluster`** (postgresql-common) is a useful reference
  but does **not** fit pg4all: it expects Debian's `pg_createcluster`
  layout, which the official `postgres` image lineage pg4all builds on does
  not use.
- **Pigsty** — the fuller project pg4all is a deliberately simpler
  alternative to, so its documented upgrade procedures are the natural
  model for our runbook: a rolling workflow for minor updates, and for
  majors a logical-replication blue/green migration (schema-only restore,
  matched extensions/collations, `wal_level=logical`, publication +
  subscription, `REPLICA IDENTITY` handling for PK-less tables, row-count/
  checksum verification) for the shortest, rollback-ready downtime, with
  `pg_upgrade` also documented.
- Kubernetes operators automate the same operations but are K8s-scoped — a
  reference for the automated/multi-host direction (the Patroni /
  `pg-custom` track), not usable in pg4all's compose model:
  - **CloudNativePG** — declarative offline in-place `pg_upgrade` major
    upgrades (v1.26+), minor by image bump.
  - **Crunchy PGO** — `PGUpgrade` CRD; permanent (no rollback), and it
    currently warns that **PostGIS upgrades are unsupported**.
  - **Percona Operator** — major upgrades GA since 2.9 (Apr 2026); offline
    (cluster stopped), one major at a time per current docs, and notably it
    **duplicates the data rather than deleting the old** (needs free space)
    — a safer stance than pgautoupgrade's auto-remove.
  - **StackGres**, **Zalando/Spilo** — also support major upgrades.

### PostgreSQL 18 image change (affects volume layout and `--link`)

From PostgreSQL 18 the official image uses a **version-specific data
directory** (`/var/lib/postgresql/18/data`, not the generic
`/var/lib/postgresql/data`), specifically so that mounting
`/var/lib/postgresql` lets `pg_upgrade --link` work across majors — making
fast in-place upgrades far more viable in the container model. PG18 also
**persists planner statistics across a major upgrade**, removing the
post-upgrade `ANALYZE` performance dip. Two consequences: pg4all's volume
layout for 18+ builds should expect the versioned path (and the current
build's volume handling should be checked against it), and the assisted
major helper can offer `--link` as a genuinely fast option, behind the
extra opt-in `--link` already requires.

## Recommended scope

| Path | Decision |
|---|---|
| Minor upgrade helper (per build) | Do it — small, safe, high value |
| Generic one-script-for-all major upgrade | Don't ship — extension/checksum/conf/rollback landmines make it unsafe |
| Per-build assisted major helper + runbook | Reasonable next step — pre-flighted via `pg_upgrade --check`, backup-mandatory, approval-required; never a silent auto-run |

## Implementation touchpoints (when built)

- Reuse `app/core/initdb.py` (checksum/collation/WAL settings the source
  used, and to initialize the target to match).
- Reuse `app/core/conf_generator.py` + `parameters.py` (version-aware) to
  regenerate the target-major conf.
- Reuse `app/core/extensions.py` (the build's extension set) for the
  version-compatibility comparison and post-upgrade `ALTER EXTENSION`.
- Reuse the credential store and, if present, pgBackRest for the backup
  step.
- Wrap `tianon/docker-postgres-upgrade` (or a pg4all dual-version image
  built the same way) for the in-place `pg_upgrade` path, and `pgcopydb`
  for the logical / near-zero-downtime path — rather than reimplementing
  either (see "Existing tools to build on").
- Account for PostgreSQL 18's version-specific data directory in the volume
  layout, which also enables the fast `pg_upgrade --link` path.
- Generate the helper scripts into `build_output/<build_id>/` alongside the
  other per-build artifacts, downloadable from the build page like the conf
  and the license notices.
