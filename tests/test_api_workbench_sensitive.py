import pytest

from notepadview.api_workbench.sensitive import is_sensitive_header


@pytest.mark.parametrize('name', [
    'Authorization',
    'proxy-authorization',
    'Cookie',
    'Set-Cookie',
    'X_API_KEY',
    'X-Session-Token',
    'X-Client-Secret',
    'User-Password',
])
def test_sensitive_header_names_are_recognized(name):
    assert is_sensitive_header(name)


@pytest.mark.parametrize('name', ['Accept', 'Content-Type', 'X-Client-Name'])
def test_ordinary_header_names_are_not_sensitive(name):
    assert not is_sensitive_header(name)
