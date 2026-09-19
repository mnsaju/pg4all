"""On-disk state for the console's own authentication.

Lives beside credential_store.py because both own files under `secrets/`,
and splitting ownership of that directory across two packages would be
worse than the slight misfit of "builder" holding something that isn't a
build artifact.

Two files, both generated on first run and both gitignored with the rest
of `secrets/`:

- `console-password.json` — an scrypt hash of the console password. The
  password itself is printed once, at startup, and never written down.
- `console-session.key` — the HMAC key that signs session cookies. It's
  separate from the Fernet key that encrypts build credentials so that
  neither one can be used against the other, and regenerating it (delete
  the file, restart) invalidates every outstanding session.
"""

import secrets as pysecrets
from pathlib import Path

from app.core import auth

SECRETS_DIR = Path(__file__).resolve().parent.parent.parent / "secrets"
PASSWORD_FILE = SECRETS_DIR / "console-password.json"
SESSION_KEY_FILE = SECRETS_DIR / "console-session.key"

SESSION_KEY_BYTES = 32


def _ensure_dir() -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)


def _write_private(path: Path, data: bytes) -> None:
    """Write owner-readable only. The directory is bind-mounted to the
    host, so these land in a real path someone could stumble across."""
    path.write_bytes(data)
    path.chmod(0o600)


def load_session_key() -> bytes:
    _ensure_dir()
    if SESSION_KEY_FILE.exists():
        return SESSION_KEY_FILE.read_bytes()

    key = pysecrets.token_bytes(SESSION_KEY_BYTES)
    _write_private(SESSION_KEY_FILE, key)
    return key


def password_is_set() -> bool:
    return PASSWORD_FILE.exists()


def load_password_record() -> auth.PasswordRecord | None:
    if not PASSWORD_FILE.exists():
        return None
    try:
        return auth.record_from_json(PASSWORD_FILE.read_text())
    except (ValueError, KeyError, OSError):
        # A corrupt or unreadable file must not silently authenticate
        # anyone; treat it as "no usable password" so login always fails
        # until it's regenerated.
        return None


def set_password(password: str) -> None:
    _ensure_dir()
    record = auth.hash_password(password)
    _write_private(PASSWORD_FILE, auth.record_to_json(record).encode("utf-8"))


def ensure_password() -> str | None:
    """Create a console password if there isn't one yet.

    Returns the new plaintext password exactly once, for printing to the
    startup log, or None if one already existed. This is the only moment
    the password exists outside the operator's hands — it is hashed on the
    way to disk and cannot be recovered afterwards. To rotate it, delete
    `secrets/console-password.json` and restart.
    """
    if password_is_set():
        return None

    password = auth.generate_password()
    set_password(password)
    return password
