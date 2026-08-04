import os

from cryptography.fernet import Fernet

_key = os.environ.get("CREDENTIALS_ENCRYPTION_KEY")
if not _key:
    raise ValueError("CREDENTIALS_ENCRYPTION_KEY is not set in .env")

_fernet = Fernet(_key.encode())


def encrypt_credentials(plaintext: str) -> str:
    """Encrypt a JSON string (e.g. tokens) before storing in Connection.credentials_encrypted."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_credentials(ciphertext: str) -> str:
    """Reverse of encrypt_credentials — call before using stored credentials."""
    return _fernet.decrypt(ciphertext.encode()).decode()