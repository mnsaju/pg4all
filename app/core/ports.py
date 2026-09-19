"""Host-side port assignments for a build's generated stack.

Every published port in the generated `docker-compose.yml` used to be
hardcoded, which works exactly until the host already has something on
that port — and 5432 in particular is the one port a machine running
PostgreSQL work is most likely to have taken already. Then the whole
stack refuses to start and the only fix is hand-editing generated files.

Only the *host* side is configurable. Container-side ports are fixed
properties of the images (PgBouncer listens on 5432 inside its container
whatever the host maps it to) and the sidecars reach Postgres over the
compose network by service name on 5432, so none of that is affected by
what a port is published as.

The `127.0.0.1` bind on the three web UIs — pgAdmin, Grafana and
Prometheus — is deliberately not configurable here. Their ports can move,
but the interface they bind to stays loopback-only: making "expose this to
the whole network" a number in a form is the kind of choice that should
take more than editing a field, and the SSH tunnel it forces is what keeps
them off the network in the first place.
"""

from dataclasses import dataclass

from app.core.validation import ERROR, WARNING, Finding

MIN_PORT = 1
MAX_PORT = 65535

# Ports below this are restricted to root on Unix hosts; binding one from
# a rootless or non-root daemon fails in a way that reads as unrelated.
PRIVILEGED_PORT_CEILING = 1024


@dataclass(frozen=True)
class PortSpec:
    key: str  # form field name, also the key used everywhere downstream
    label: str
    default: int
    container_port: int
    # None means "publish on all interfaces", the Docker default.
    bind_address: str | None = None
    # The service this port belongs to, or None for the Postgres container
    # itself, which every generated stack has.
    service_key: str | None = None

    def published(self, host_port: int) -> str:
        """The compose `ports:` entry for this assignment."""
        if self.bind_address:
            return f"{self.bind_address}:{host_port}:{self.container_port}"
        return f"{host_port}:{self.container_port}"


POSTGRES_PORT_KEY = "postgres"

PORT_SPECS: list[PortSpec] = [
    PortSpec(POSTGRES_PORT_KEY, "PostgreSQL", default=5432, container_port=5432),
    PortSpec(
        "pgbouncer", "PgBouncer", default=6432, container_port=5432,
        service_key="pgbouncer",
    ),
    PortSpec(
        "postgres_exporter", "Metrics exporter", default=9187, container_port=9187,
        service_key="postgres_exporter",
    ),
    PortSpec(
        "pgadmin", "pgAdmin", default=5050, container_port=80,
        bind_address="127.0.0.1", service_key="pgadmin",
    ),
    # Both monitoring UIs are loopback-only for the same reason pgAdmin is,
    # and Prometheus more urgently: it has no authentication at all, and its
    # UI serves every metric plus its own running config to anyone who can
    # reach it. Published rather than hidden entirely because "is the target
    # up?" is the first question when a dashboard comes up empty.
    PortSpec(
        "prometheus", "Prometheus", default=9090, container_port=9090,
        bind_address="127.0.0.1", service_key="prometheus",
    ),
    PortSpec(
        "grafana", "Grafana", default=3000, container_port=3000,
        bind_address="127.0.0.1", service_key="grafana",
    ),
]

_BY_KEY = {p.key: p for p in PORT_SPECS}


def get(key: str) -> PortSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown port: {key}")


def specs_for(service_keys) -> list[PortSpec]:
    """The ports an actual stack will publish: Postgres, plus the selected
    sidecars. A service that isn't selected publishes nothing, so its port
    is neither used nor checked for conflicts."""
    selected = set(service_keys)
    return [
        spec for spec in PORT_SPECS
        if spec.service_key is None or spec.service_key in selected
    ]


def defaults() -> dict[str, int]:
    return {spec.key: spec.default for spec in PORT_SPECS}


def resolve(raw: dict[str, str | None]) -> dict[str, int]:
    """Submitted host ports, falling back to the default for anything
    missing or unparseable — same defensive pattern as the parameter
    sliders, so a partial POST can't produce a half-formed compose file.
    Values are range-checked by `validate`, not silently clamped here."""
    resolved = {}
    for spec in PORT_SPECS:
        value = raw.get(spec.key)
        try:
            resolved[spec.key] = int(value) if value not in (None, "") else spec.default
        except (TypeError, ValueError):
            resolved[spec.key] = spec.default
    return resolved


def validate(
    ports: dict[str, int],
    service_keys,
    ports_in_use: dict[int, str] | None = None,
) -> list[Finding]:
    """Check the host ports an actual stack would publish.

    `ports_in_use` maps a host port to what already holds it, as reported
    by the Docker daemon. Conflicts with it are warnings rather than
    errors: the image builds regardless, and the operator may be about to
    stop whatever is holding the port. A port that's out of range or
    claimed twice in the same stack is an error — that compose file can
    never start, whatever else is running.
    """
    ports_in_use = ports_in_use or {}
    active = specs_for(service_keys)
    findings: list[Finding] = []

    for spec in active:
        port = ports[spec.key]
        if not MIN_PORT <= port <= MAX_PORT:
            findings.append(
                Finding(
                    level=ERROR,
                    parameter_keys=(f"port_{spec.key}",),
                    summary=f"{spec.label} host port {port} is out of range",
                    detail=(
                        f"A TCP port is {MIN_PORT}-{MAX_PORT}. The generated "
                        "compose file would be rejected before anything starts."
                    ),
                )
            )

    by_port: dict[int, list[PortSpec]] = {}
    for spec in active:
        by_port.setdefault(ports[spec.key], []).append(spec)

    for port, specs in by_port.items():
        if len(specs) > 1:
            names = ", ".join(s.label for s in specs)
            findings.append(
                Finding(
                    level=ERROR,
                    parameter_keys=tuple(f"port_{s.key}" for s in specs),
                    summary=f"{names} all want host port {port}",
                    detail=(
                        "Two containers in the same stack can't publish the "
                        "same host port. The second one to start would fail "
                        "to bind and the stack would come up incomplete."
                    ),
                )
            )

    for spec in active:
        port = ports[spec.key]
        if port in ports_in_use:
            findings.append(
                Finding(
                    level=WARNING,
                    parameter_keys=(f"port_{spec.key}",),
                    summary=(
                        f"Host port {port} ({spec.label}) is already taken by "
                        f"{ports_in_use[port]}"
                    ),
                    detail=(
                        "The image will build fine, but `docker compose up` "
                        "won't bind this port until that container is stopped "
                        "— pick another port, or stop it first."
                    ),
                )
            )
        elif port < PRIVILEGED_PORT_CEILING:
            findings.append(
                Finding(
                    level=WARNING,
                    parameter_keys=(f"port_{spec.key}",),
                    summary=f"{spec.label} host port {port} is privileged",
                    detail=(
                        f"Ports below {PRIVILEGED_PORT_CEILING} need root on a "
                        "Unix host. A rootless Docker daemon can't bind one, "
                        "and the failure doesn't say so clearly."
                    ),
                )
            )

    return sorted(findings, key=lambda f: 0 if f.level == ERROR else 1)
