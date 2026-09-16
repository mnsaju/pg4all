"""Superuser-password generation and the credential record shape.

Pure logic only — no file I/O, no encryption (that's
app/builder/credential_store.py). The username is always "postgres": pg4all
reuses the stock superuser the official postgres image already creates,
rather than inventing a new one. Only the password is generated, and it's
never baked into the built image — it's handed to the operator to pass as
POSTGRES_PASSWORD on `docker run`, the same mechanism the official image
already uses to set that password on first init.
"""

import json
import secrets
from dataclasses import asdict, dataclass

ADMIN_USERNAME = "postgres"


def generate_password(length_bytes: int = 18) -> str:
    # token_urlsafe's alphabet (letters, digits, "-", "_") has no
    # shell-special characters, so the result is safe to embed in
    # `-e POSTGRES_PASSWORD=...` without quoting concerns.
    return secrets.token_urlsafe(length_bytes)


@dataclass(frozen=True)
class CredentialRecord:
    build_id: str
    username: str
    password: str
    image_tag: str
    pg_major: str
    created_at: str  # ISO-8601 UTC
    build_ok: bool


def to_json(record: CredentialRecord) -> str:
    return json.dumps(asdict(record))


def from_json(raw: str) -> CredentialRecord:
    return CredentialRecord(**json.loads(raw))
