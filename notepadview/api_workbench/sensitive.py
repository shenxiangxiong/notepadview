def is_sensitive_header(name: str) -> bool:
    normalized = name.casefold().replace('_', '-')
    return normalized in {
        'authorization', 'proxy-authorization', 'cookie', 'set-cookie',
        'x-api-key', 'api-key',
    } or any(word in normalized for word in ('token', 'secret', 'password'))
