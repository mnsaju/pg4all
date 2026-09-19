"""Password hashing and session tokens for the console's single operator.

Pure logic only — no file I/O and no FastAPI, same split as
app/core/credentials.py against app/builder/credential_store.py. Storage
lives in app/builder/auth_store.py.

There is one operator. This is deliberately not a user system: no
accounts, no roles, no registration, no password reset flow. The console
is a single-operator tool and giving it a user table would be inventing a
problem. What it needs is for an unauthenticated request to get nothing,
because `/credentials/<build_id>` hands out Postgres superuser passwords
in cleartext and the process holds the host's Docker socket.

The password is never stored, only an scrypt hash of it. scrypt is
deliberately slow and memory-hard, which is also the only brute-force
defence here: there's no lockout or attempt counter, because a lockout on
a single-operator tool is mostly a way to lock out the operator, and the
console isn't reachable from the network anyway (it binds to loopback).
"""

import base64
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# scrypt cost. n must be a power of two; n=2^15 with r=8 needs about 32 MB
# per hash, which is a comfortable fraction of a second on a normal host —
# slow enough to make guessing expensive, fast enough that a login doesn't
# feel broken.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_LENGTH = 16

# How long a session stays valid. Long enough to work through a build
# without re-authenticating, short enough that a forgotten browser tab on
# a shared machine doesn't stay usable indefinitely.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

SESSION_COOKIE_NAME = "pg4all_session"


@dataclass(frozen=True)
class PasswordRecord:
    """An scrypt hash plus the parameters actually used to produce it.

    n/r/p are required rather than defaulted. A dataclass default is
    evaluated once at import, so defaulting them to the module constants
    would let a record claim parameters the hash wasn't derived with the
    moment those constants differ from what the caller passed — and a
    record whose stated cost doesn't match its hash can never be verified
    again. Storing them per-record is also what lets the cost be raised
    later without invalidating existing passwords.
    """

    salt: bytes
    hash: bytes
    n: int
    r: int
    p: int


def generate_password(length_bytes: int = 18) -> str:
    """A fresh console password. 18 bytes of entropy, URL-safe so it
    survives being copied out of a log line."""
    return secrets.token_urlsafe(length_bytes)


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_LENGTH, n=n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def hash_password(password: str) -> PasswordRecord:
    # Read the constants once and record exactly what was used, so the
    # record and the hash can never disagree about the cost.
    n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
    salt = secrets.token_bytes(SALT_LENGTH)
    return PasswordRecord(salt=salt, hash=_derive(password, salt, n, r, p), n=n, r=r, p=p)


def verify_password(password: str, record: PasswordRecord) -> bool:
    """Constant-time check of a candidate password against a stored hash."""
    try:
        candidate = _derive(password, record.salt, record.n, record.r, record.p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, record.hash)


def record_to_json(record: PasswordRecord) -> str:
    return json.dumps(
        {
            "algorithm": "scrypt",
            "n": record.n,
            "r": record.r,
            "p": record.p,
            "salt": base64.b64encode(record.salt).decode("ascii"),
            "hash": base64.b64encode(record.hash).decode("ascii"),
        },
        indent=2,
    )


def record_from_json(raw: str) -> PasswordRecord:
    data = json.loads(raw)
    if data.get("algorithm") != "scrypt":
        raise ValueError(f"Unsupported password algorithm: {data.get('algorithm')!r}")
    return PasswordRecord(
        salt=base64.b64decode(data["salt"]),
        hash=base64.b64decode(data["hash"]),
        n=int(data["n"]),
        r=int(data["r"]),
        p=int(data["p"]),
    )


def issue_session(signing_key: bytes, issued_at: float | None = None) -> str:
    """A signed session token: the issue time, plus an HMAC over it.

    Nothing secret is carried in the token — there is only one operator, so
    there is no identity to encode. The signature is what makes it
    unforgeable and the timestamp is what makes it expire.
    """
    issued = str(int(issued_at if issued_at is not None else time.time()))
    signature = hmac.new(signing_key, issued.encode("ascii"), sha256).hexdigest()
    return f"{issued}.{signature}"


def verify_session(
    token: str | None,
    signing_key: bytes,
    now: float | None = None,
    max_age: int = SESSION_MAX_AGE_SECONDS,
) -> bool:
    if not token:
        return False

    issued_text, _, signature = token.partition(".")
    if not signature:
        return False

    expected = hmac.new(signing_key, issued_text.encode("ascii"), sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False

    # Only trusted after the signature checks out, so a forged timestamp
    # can't buy an attacker anything.
    try:
        issued = int(issued_text)
    except ValueError:
        return False

    now = now if now is not None else time.time()
    # A token issued in the future means a clock moved or someone is
    # replaying something odd; either way it isn't valid.
    return 0 <= (now - issued) <= max_age
