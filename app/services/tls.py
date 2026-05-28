from __future__ import annotations

import ssl

import certifi


def build_ssl_context() -> ssl.SSLContext:
    """Build an SSL context without disabling certificate verification.

    On Windows, school networks and antivirus tools often install a local root
    CA into the system trust store. `truststore` lets httpx use that store when
    it is available; certifi remains the portable fallback.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context(cafile=certifi.where())


def is_certificate_verify_error(exc: BaseException) -> bool:
    text = repr(exc)
    current: BaseException | None = exc
    while current is not None:
        text += " " + repr(current)
        current = current.__cause__
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text
