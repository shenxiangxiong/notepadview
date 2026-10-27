import pytest


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value


def _secret_vault(*args, **kwargs):
    try:
        from notepadview.api_workbench.secrets import SecretVault
    except ImportError:
        pytest.fail('API secret vault is not implemented')
    return SecretVault(*args, **kwargs)


def test_secret_vault_encrypts_and_reuses_os_protected_master_key():
    keyring = MemoryKeyring()
    first = _secret_vault(keyring_backend=keyring)
    ciphertext = first.encrypt(b'sensitive-token')

    reopened = _secret_vault(keyring_backend=keyring)
    assert ciphertext != b'sensitive-token'
    assert b'sensitive-token' not in ciphertext
    assert reopened.decrypt(ciphertext) == b'sensitive-token'


def test_secret_vault_rejects_tampered_ciphertext():
    vault = _secret_vault(keyring_backend=MemoryKeyring())
    ciphertext = bytearray(vault.encrypt(b'secret'))
    ciphertext[-1] ^= 1

    with pytest.raises(ValueError, match='decrypt'):
        vault.decrypt(bytes(ciphertext))


def test_secret_vault_does_not_fall_back_when_keyring_fails():
    class BrokenKeyring:
        def get_password(self, service, username):
            raise RuntimeError('keyring unavailable')

        def set_password(self, service, username, value):
            raise RuntimeError('keyring unavailable')

    vault = _secret_vault(keyring_backend=BrokenKeyring())
    with pytest.raises(RuntimeError, match='keyring'):
        vault.encrypt(b'secret')
