import base64
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_KEY_LENGTH = 32
_NONCE_LENGTH = 12
_CIPHERTEXT_PREFIX = b'NVAPI1'
_KEYRING_SERVICE = 'Notepad View API Workbench'
_KEYRING_USER = 'api-workbench-master-key'
_AUTHENTICATED_DATA = b'notepadview-api-workbench-secret-v1'


class SecretStorageError(RuntimeError):
    pass


class SecretVault:
    def __init__(self, keyring_backend=None):
        if keyring_backend is None:
            import keyring
            keyring_backend = keyring
            backend = keyring.get_keyring()
            if getattr(backend, 'priority', 0) <= 0:
                raise SecretStorageError('No secure operating-system keyring is available')
        self._keyring = keyring_backend

    def _master_key(self):
        encoded = self._keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if encoded is None:
            encoded = base64.urlsafe_b64encode(secrets.token_bytes(_KEY_LENGTH)).decode('ascii')
            self._keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, encoded)
            stored = self._keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
            if stored is None:
                raise SecretStorageError('Operating-system keyring did not retain the master key')
            encoded = stored
        try:
            key = base64.b64decode(encoded.encode('ascii'), altchars=b'-_', validate=True)
        except (UnicodeError, ValueError) as error:
            raise SecretStorageError('Operating-system keyring contains an invalid master key') from error
        if len(key) != _KEY_LENGTH:
            raise SecretStorageError('Operating-system keyring contains an invalid master key')
        return key

    def encrypt(self, value):
        if not isinstance(value, bytes):
            raise TypeError('SecretVault.encrypt expects bytes')
        nonce = secrets.token_bytes(_NONCE_LENGTH)
        encrypted = AESGCM(self._master_key()).encrypt(nonce, value, _AUTHENTICATED_DATA)
        return _CIPHERTEXT_PREFIX + nonce + encrypted

    def decrypt(self, value):
        if not isinstance(value, bytes) or not value.startswith(_CIPHERTEXT_PREFIX):
            raise ValueError('Unable to decrypt secret: invalid ciphertext format')
        nonce_start = len(_CIPHERTEXT_PREFIX)
        nonce_end = nonce_start + _NONCE_LENGTH
        if len(value) <= nonce_end:
            raise ValueError('Unable to decrypt secret: invalid ciphertext length')
        try:
            return AESGCM(self._master_key()).decrypt(
                value[nonce_start:nonce_end], value[nonce_end:], _AUTHENTICATED_DATA)
        except InvalidTag as error:
            raise ValueError('Unable to decrypt secret: authentication failed') from error
