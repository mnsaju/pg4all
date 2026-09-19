"""Tests for app/core/ports.py — host-port assignment and its checks."""

from app.core import ports, validation


def summaries(findings) -> str:
    return " | ".join(f.summary for f in findings)


def test_defaults_match_the_conventional_ports():
    assert ports.defaults() == {
        "postgres": 5432,
        "pgbouncer": 6432,
        "postgres_exporter": 9187,
        "pgadmin": 5050,
    }


def test_published_entry_maps_host_to_container_port():
    assert ports.get("pgbouncer").published(6432) == "6432:5432"
    assert ports.get("postgres").published(15432) == "15432:5432"


def test_pgadmin_keeps_its_loopback_bind_at_any_port():
    """Only the port is configurable. Publishing the admin UI to every
    interface should take more than typing a number into a form."""
    assert ports.get("pgadmin").published(5050) == "127.0.0.1:5050:80"
    assert ports.get("pgadmin").published(9999) == "127.0.0.1:9999:80"


def test_only_selected_services_have_a_published_port():
    keys = [s.key for s in ports.specs_for({"pgbouncer"})]
    assert keys == ["postgres", "pgbouncer"]
    assert [s.key for s in ports.specs_for(set())] == ["postgres"]


def test_resolve_falls_back_to_defaults_for_missing_or_junk_values():
    resolved = ports.resolve(
        {"postgres": "15432", "pgbouncer": "", "pgadmin": "not-a-port"}
    )
    assert resolved["postgres"] == 15432
    assert resolved["pgbouncer"] == 6432
    assert resolved["pgadmin"] == 5050
    assert resolved["postgres_exporter"] == 9187


def test_defaults_are_clean():
    assert ports.validate(ports.defaults(), {"pgbouncer", "pgadmin"}) == []


def test_out_of_range_port_is_an_error():
    values = {**ports.defaults(), "postgres": 70000}
    findings = ports.validate(values, set())

    assert any(f.level == validation.ERROR for f in findings)
    assert "out of range" in summaries(findings)


def test_two_services_on_the_same_port_is_an_error():
    values = {**ports.defaults(), "pgbouncer": 5432}
    findings = ports.validate(values, {"pgbouncer"})

    assert any(f.level == validation.ERROR for f in findings)
    assert "want host port 5432" in summaries(findings)


def test_a_collision_only_counts_when_both_services_are_selected():
    """An unselected service publishes nothing, so its port can't clash."""
    values = {**ports.defaults(), "pgbouncer": 5432}
    assert ports.validate(values, set()) == []


def test_a_port_held_by_a_running_container_warns_without_blocking():
    """The image builds fine; only `docker compose up` would fail, and the
    operator may be about to stop whatever is holding the port."""
    findings = ports.validate(
        ports.defaults(), set(), ports_in_use={5432: "ismr-postgres"}
    )

    assert not any(f.level == validation.ERROR for f in findings)
    assert "already taken by ismr-postgres" in summaries(findings)


def test_a_privileged_port_warns():
    values = {**ports.defaults(), "postgres": 443}
    findings = ports.validate(values, set())

    assert not any(f.level == validation.ERROR for f in findings)
    assert "is privileged" in summaries(findings)


def test_errors_sort_ahead_of_warnings():
    values = {**ports.defaults(), "postgres": 70000, "pgbouncer": 80}
    findings = ports.validate(values, {"pgbouncer"})

    levels = [f.level for f in findings]
    assert levels[0] == validation.ERROR
    assert validation.WARNING in levels
