import re

import pytest

from app.builder import credential_store
from app.core import credentials


def test_generate_password_has_no_shell_special_characters():
    password = credentials.generate_password()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", password)
    assert len(password) >= 20


def test_generate_password_is_random():
    assert credentials.generate_password() != credentials.generate_password()


def _sample_record(build_id: str = "abc123") -> credentials.CredentialRecord:
    return credentials.CredentialRecord(
        build_id=build_id,
        username=credentials.ADMIN_USERNAME,
        password="s3cret-token",
        image_tag="pg4all/postgres:17-oltp-medium",
        pg_major="17",
        created_at="2026-09-16T00:00:00+00:00",
        build_ok=True,
    )


def test_record_json_round_trip():
    record = _sample_record()
    restored = credentials.from_json(credentials.to_json(record))
    assert restored == record


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(credential_store, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(credential_store, "KEY_FILE", tmp_path / "secrets" / "master.key")
    monkeypatch.setattr(credential_store, "CREDENTIALS_DIR", tmp_path / "credentials")
    return tmp_path


def test_save_and_load_round_trips(isolated_store):
    record = _sample_record()
    credential_store.save_credential(record)
    assert credential_store.load_credential(record.build_id) == record


def test_stored_file_is_not_plaintext(isolated_store):
    record = _sample_record()
    credential_store.save_credential(record)
    raw = (credential_store.CREDENTIALS_DIR / f"{record.build_id}.enc").read_bytes()
    assert record.password.encode() not in raw
    assert b"CredentialRecord" not in raw


def test_load_unknown_build_id_returns_none(isolated_store):
    assert credential_store.load_credential("does-not-exist") is None


def test_key_is_generated_once_and_reused(isolated_store):
    first = credential_store._load_or_create_key()
    second = credential_store._load_or_create_key()
    assert first == second
