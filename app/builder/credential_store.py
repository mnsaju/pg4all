"""Encrypted, on-disk storage for per-build superuser credentials.

pg4all's first piece of persisted state. The encryption key is generated
locally on first use and stored next to the ciphertext (0600, gitignored) —
zero setup, consistent with pg4all's single-trusted-operator model, but
this only protects against casual exposure (backups, accidental commits,
screenshots): anyone with read access to this host's filesystem can read
both the key and the data. That's a stated limit, not an oversight.
"""

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.credentials import CredentialRecord, from_json, to_json

_ROOT = Path(__file__).resolve().parent.parent.parent
SECRETS_DIR = _ROOT / "secrets"
KEY_FILE = SECRETS_DIR / "master.key"
CREDENTIALS_DIR = _ROOT / "credentials"


def _load_or_create_key() -> bytes:
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    return key


def save_credential(record: CredentialRecord) -> None:
    fernet = Fernet(_load_or_create_key())
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    ciphertext = fernet.encrypt(to_json(record).encode())
    path = CREDENTIALS_DIR / f"{record.build_id}.enc"
    path.write_bytes(ciphertext)
    path.chmod(0o600)


def load_credential(build_id: str) -> CredentialRecord | None:
    path = CREDENTIALS_DIR / f"{build_id}.enc"
    if not path.exists():
        return None

    fernet = Fernet(_load_or_create_key())
    try:
        plaintext = fernet.decrypt(path.read_bytes())
    except InvalidToken:
        return None
    return from_json(plaintext.decode())


def delete_credential(build_id: str) -> bool:
    """Remove one build's stored credential. Returns whether it existed.

    The counterpart the store never had: it only ever grew, so every build
    ever made kept its superuser password on disk indefinitely, including
    for images long since deleted.
    """
    if not build_id or "/" in build_id or "\\" in build_id or build_id.startswith("."):
        raise ValueError(f"Not a build id: {build_id!r}")

    path = CREDENTIALS_DIR / f"{build_id}.enc"
    if not path.exists():
        return False
    path.unlink()
    return True


def list_credentials() -> list[CredentialRecord]:
    """All stored build records, most recent first.

    Decrypts every file to read its `created_at` — fine at the build
    volumes a single trusted operator produces, but would need an
    unencrypted index if that volume ever grew.
    """
    if not CREDENTIALS_DIR.exists():
        return []

    fernet = Fernet(_load_or_create_key())
    records = []
    for path in CREDENTIALS_DIR.glob("*.enc"):
        try:
            plaintext = fernet.decrypt(path.read_bytes())
        except InvalidToken:
            continue
        records.append(from_json(plaintext.decode()))

    records.sort(key=lambda r: r.created_at, reverse=True)
    return records
