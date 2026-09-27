"""Development-only capture of the exact JSON body sent to an LLM.

Authorization headers are intentionally excluded. These records can contain student
work and conversation text, so the read API is limited to local development.
"""

import json
import logging

from ..config import settings
from ..database import database, utc_now


def record_llm_request(channel: str, purpose: str, provider: str, endpoint: str, body: dict) -> None:
    if settings.app_env != "development":
        return
    try:
        with database() as connection:
            connection.execute(
                """INSERT INTO llm_request_logs
                   (channel, purpose, provider, endpoint, request_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (channel, purpose, provider, endpoint, json.dumps(body, ensure_ascii=False), utc_now()),
            )
    except Exception:
        # Debug capture must never prevent a teaching or student request.
        logging.exception("Could not record development LLM request")
