"""Tests for app/core/auth.py — password hashing and session tokens.

Pure crypto logic, no FastAPI and no disk. The scrypt parameters are
lowered where a test only cares about round-tripping, because the real
cost (32 MB per hash) is the point in production and just slow here.
"""

import time

import pytest

from app.core import auth


def test_a_password_verifies_against_its_own_hash():
    record = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", record)


def test_a_wrong_password_does_not_verify():
    record = auth.hash_password("correct horse battery staple")
    assert not auth.verify_password("Correct horse battery staple", record)
    assert not auth.verify_password("", record)


def test_the_same_password_hashes_differently_each_time():
    """Distinct salts, so identical passwords don't produce identical
    hashes and the file gives nothing away by comparison."""
    first = auth.hash_password("same")
    second = auth.hash_password("same")
    assert first.salt != second.salt
    assert first.hash != second.hash
    assert auth.verify_password("same", first)
    assert auth.verify_password("same", second)


def test_the_plaintext_password_is_nowhere_in_the_stored_record():
    password = "sup3r-s3cret-console"
    serialized = auth.record_to_json(auth.hash_password(password))
    assert password not in serialized
    assert "scrypt" in serialized


def test_a_record_survives_a_json_round_trip():
    record = auth.hash_password("round trip")
    restored = auth.record_from_json(auth.record_to_json(record))
    assert restored == record
    assert auth.verify_password("round trip", restored)


def test_an_unknown_hash_algorithm_is_rejected():
    """Better to fail loudly than to silently treat an unrecognised record
    as something scrypt can check."""
    with pytest.raises(ValueError):
        auth.record_from_json('{"algorithm": "md5", "salt": "", "hash": "", '
                              '"n": 1, "r": 1, "p": 1}')


def test_generated_passwords_are_unique_and_substantial():
    passwords = {auth.generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) >= 20 for p in passwords)


def test_a_freshly_issued_session_verifies():
    key = b"k" * 32
    assert auth.verify_session(auth.issue_session(key), key)


def test_a_session_signed_with_another_key_is_rejected():
    token = auth.issue_session(b"k" * 32)
    assert not auth.verify_session(token, b"different key" + b"x" * 19)


def test_a_tampered_session_is_rejected():
    key = b"k" * 32
    issued, _, signature = auth.issue_session(key).partition(".")

    # Move the clock forward in the payload without re-signing it.
    forged = f"{int(issued) + 10_000}.{signature}"
    assert not auth.verify_session(forged, key)


def test_a_malformed_session_is_rejected():
    key = b"k" * 32
    for token in (None, "", "nodot", ".", "abc.def", "12345."):
        assert not auth.verify_session(token, key), token


def test_a_session_expires():
    key = b"k" * 32
    issued_at = time.time()
    token = auth.issue_session(key, issued_at=issued_at)

    just_inside = issued_at + auth.SESSION_MAX_AGE_SECONDS - 1
    just_outside = issued_at + auth.SESSION_MAX_AGE_SECONDS + 1

    assert auth.verify_session(token, key, now=just_inside)
    assert not auth.verify_session(token, key, now=just_outside)


def test_a_session_issued_in_the_future_is_rejected():
    """A clock that moved, or a replay of something odd — either way it
    isn't a token this process handed out a moment ago."""
    key = b"k" * 32
    now = time.time()
    token = auth.issue_session(key, issued_at=now + 5_000)
    assert not auth.verify_session(token, key, now=now)


def test_a_record_stores_the_parameters_actually_used(monkeypatch):
    """The dataclass must not default n/r/p to the module constants: a
    default is evaluated once at import, so a record could claim a cost the
    hash wasn't derived with and then never verify again. Raising the cost
    later must also leave existing passwords working."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2**8)
    record = auth.hash_password("cheap")
    assert record.n == 2**8
    assert auth.verify_password("cheap", record)

    # Cost goes up for new passwords; the old record still verifies at its own.
    monkeypatch.setattr(auth, "SCRYPT_N", 2**10)
    assert auth.verify_password("cheap", record)
    assert auth.hash_password("dear").n == 2**10
