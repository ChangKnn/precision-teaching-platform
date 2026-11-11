from __future__ import annotations

import os
import ssl
import time
from urllib.error import URLError
from pathlib import Path


def verified_ssl_context() -> ssl.SSLContext:
    """Return a verified TLS context, including the macOS system CA bundle when needed."""
    configured_bundle = os.getenv("AI_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    candidates = [configured_bundle, "/etc/ssl/cert.pem"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def open_with_tls_retry(request, timeout, opener):
    """Retry one TLS setup EOF only; never disable certificate verification."""
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        try:
            return opener(request, timeout=max(1, deadline - time.monotonic()), context=verified_ssl_context())
        except (URLError, ssl.SSLError) as error:
            reason = getattr(error, 'reason', error)
            eof = isinstance(reason, ssl.SSLEOFError) or (isinstance(reason, ssl.SSLError) and 'UNEXPECTED_EOF_WHILE_READING' in str(reason))
            if not eof:
                raise
            if attempt or deadline - time.monotonic() <= 1:
                raise RuntimeError('与 AI 服务的安全连接中断，请稍后重试') from error
            time.sleep(0.25)
