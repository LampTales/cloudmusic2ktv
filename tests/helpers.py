from __future__ import annotations

import inspect
from typing import Any


def set_session_cookie(client: Any, token: str) -> None:
    """Set a test-client cookie across Werkzeug 2.x and 3.x signatures."""
    if "server_name" in inspect.signature(client.set_cookie).parameters:
        client.set_cookie("localhost", "cloudmusic2ktv_session", token)
    else:
        client.set_cookie("cloudmusic2ktv_session", token, domain="localhost")
