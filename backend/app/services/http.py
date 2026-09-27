from __future__ import annotations

import os
import ssl
from pathlib import Path


def verified_ssl_context() -> ssl.SSLContext:
    """Return a verified TLS context, including the macOS system CA bundle when needed."""
    configured_bundle = os.getenv("AI_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    candidates = [configured_bundle, "/etc/ssl/cert.pem"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()
