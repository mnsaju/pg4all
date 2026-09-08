"""Workload types, following PGTune's own categories so the sizing
formulas in conf_generator.py can be ported directly instead of
re-derived."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    key: str
    label: str
    description: str


WORKLOADS: list[Workload] = [
    Workload("web", "Web application", "Many short, simple queries; many concurrent connections."),
    Workload("oltp", "OLTP", "Highly concurrent, transaction-heavy, moderate query complexity."),
    Workload("dw", "Data warehouse / OLAP", "Few concurrent, complex, long-running analytical queries."),
    Workload("mixed", "Mixed", "A blend of OLTP and reporting on the same instance."),
    Workload("desktop", "Desktop / dev", "Single-user, minimal resource footprint."),
]

_BY_KEY = {w.key: w for w in WORKLOADS}


def get(key: str) -> Workload:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown workload: {key}")
