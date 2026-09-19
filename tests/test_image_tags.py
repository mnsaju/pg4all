"""Tests for app/core/image_tags.py."""

from app.core import image_tags

BUILD_ID = "2dc19e6717144d9f8e5a1e1aaf6f6071"


def test_the_series_tag_is_the_old_short_name():
    assert image_tags.series_tag("17", "oltp", "medium") == (
        "pg4all/postgres:17-oltp-medium"
    )


def test_a_build_tag_carries_the_build_id():
    assert image_tags.build_tag("17", "oltp", "medium", BUILD_ID) == (
        "pg4all/postgres:17-oltp-medium-2dc19e67"
    )


def test_builds_of_the_same_combination_get_different_tags():
    """The whole point: the tag named a category while a build is an event,
    so every rebuild silently took the previous build's image."""
    first = image_tags.build_tag("17", "oltp", "medium", "a" * 32)
    second = image_tags.build_tag("17", "oltp", "medium", "b" * 32)
    assert first != second


def test_a_build_tag_sits_under_its_series_tag():
    series = image_tags.series_tag("17", "oltp", "medium")
    assert image_tags.build_tag("17", "oltp", "medium", BUILD_ID).startswith(series + "-")


def test_build_tags_are_recognisable_and_series_tags_are_not():
    """Delete has to tell them apart: removing a series tag would take
    whatever image currently holds that name."""
    assert image_tags.is_build_tag("pg4all/postgres:17-oltp-medium-2dc19e67")
    assert not image_tags.is_build_tag("pg4all/postgres:17-oltp-medium")
    assert not image_tags.is_build_tag("pg4all/postgres:16-dw-large")
    assert not image_tags.is_build_tag("")
    assert not image_tags.is_build_tag("postgres:17")
    # Right shape, wrong alphabet — a workload could never be hex-only.
    assert not image_tags.is_build_tag("pg4all/postgres:17-oltp-medium-zzzzzzzz")
    assert not image_tags.is_build_tag("pg4all/postgres:17-oltp-medium-2dc19e6")


def test_tags_are_valid_docker_references():
    """Docker tags allow [a-zA-Z0-9_][a-zA-Z0-9._-]{0,127} after the colon."""
    import re

    for tag in (
        image_tags.series_tag("18", "dw", "large"),
        image_tags.build_tag("18", "dw", "large", BUILD_ID),
    ):
        _, _, version = tag.partition(":")
        assert re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}", version), tag
