"""Rate limiting, admin sessions, and validation of values that end up in HTTP headers."""

import hashlib
import hmac
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app import config

SESSION_COOKIE = "admin_session"
SESSION_SECONDS = 12 * 60 * 60


class RateLimiter:
    """Sliding-window limiter kept in memory. Use Redis instead once you run more than one server."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers so this is the visitor's IP.
    return request.client.host if request.client else "unknown"


def _sign(value: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    expires = str(int(time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}"


def session_is_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not hmac.compare_digest(signature, _sign(expires)):
        return False
    return expires.isdigit() and int(expires) > time.time()


def password_matches(candidate: str) -> bool:
    if not config.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(candidate.encode(), config.ADMIN_PASSWORD.encode())


def require_admin(request: Request) -> None:
    """FastAPI dependency for every admin API route."""
    if not session_is_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(401, "Not logged in")


# scheme://host[:port] and nothing else. This value is written into a
# Content-Security-Policy header, so anything looser could inject directives.
_ORIGIN_RE = re.compile(r"^https?://(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_BOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def parse_origins(raw: str) -> list[str]:
    """Split the admin's newline/comma-separated list into validated origins, or raise ValueError."""
    origins = []
    for item in re.split(r"[\s,]+", raw.strip()):
        if not item:
            continue
        item = item.rstrip("/")
        if not _ORIGIN_RE.match(item):
            raise ValueError(f"'{item}' is not a valid origin. Use the form https://example.com")
        origins.append(item.lower())
    return origins


def is_valid_color(value: str) -> bool:
    return bool(_COLOR_RE.match(value))


def is_valid_bot_id(value: str) -> bool:
    return bool(_BOT_ID_RE.match(value))


def frame_ancestors_policy(allowed_origins: list[str]) -> str:
    """CSP telling browsers which sites may show the chat iframe.

    'self' lets the admin dashboard preview the bot; every other site must be listed.
    """
    return " ".join(["frame-ancestors 'self'", *allowed_origins])
