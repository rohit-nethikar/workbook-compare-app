"""Fernet-based encryption for the Tableau PAT at rest.

The key lives in instance/secret.key (created on first run, outside any git repo -
instance/ is gitignored). The encrypted PAT itself is stored in the SQLite app_config
table. Neither the plaintext PAT nor the key ever gets written to config.yaml or logs.
"""
import os
import stat
from cryptography.fernet import Fernet

from config import SECRET_KEY_PATH


def _load_or_create_key() -> bytes:
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "rb") as f:
            return f.read()

    key = Fernet.generate_key()
    with open(SECRET_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRET_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # best-effort on Windows; NTFS ACLs already restrict this to the owning user account
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


def encrypt_value(plaintext: str) -> str:
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    plaintext = _fernet().decrypt(ciphertext.encode("utf-8"))
    return plaintext.decode("utf-8")
