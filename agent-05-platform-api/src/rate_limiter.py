"""
Nigeria Pharmacy Registry — In-Memory Sliding Window Rate Limiter

Per-tier rate limits (requests per minute):
    public:          60
    registry_read:  300
    registry_write: 300
    admin:          600

Keys: API key ID for authenticated users, client IP for anonymous.

Response headers on every response:
    X-RateLimit-Limit     — max requests per window
    X-RateLimit-Remaining — requests left
    X-RateLimit-Reset     — seconds until window resets

Returns 429 Too Many Requests with Retry-After header when exceeded.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier-based limits (requests per 60-second window)
# ---------------------------------------------------------------------------

TIER_LIMITS: dict[str, int] = {
    "public": 60,
    "registry_read": 300,
    "registry_write": 300,
    "admin": 600,
}

DEFAULT_LIMIT = 60
WINDOW_SECONDS = 60

# ---------------------------------------------------------------------------
# Sliding window store
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_store: dict[str, list[float]] = {}  # key -> list of request timestamps
_last_cleanup = time.time()
_CLEANUP_INTERVAL = 60  # seconds between cleanups


def _get_client_key(request: Request) -> str:
    """Derive rate-limit key from request (API key ID or IP)."""
    auth = getattr(request.state, "auth", None)
    if auth and auth.key_id:
        return f"key:{auth.key_id}"

    # Fall back to client IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def _get_limit(request: Request) -> int:
    """Get the rate limit for this request based on tier and overrides."""
    auth = getattr(request.state, "auth", None)
    if not auth:
        return DEFAULT_LIMIT

    # Check for per-key override
    # (would be loaded from api_keys.rate_limit_override in auth.py)
    # For now, use tier defaults
    return TIER_LIMITS.get(auth.tier, DEFAULT_LIMIT)


def _cleanup_expired() -> None:
    """Remove expired entries from the store (called periodically)."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now

    cutoff = now - WINDOW_SECONDS
    expired_keys = []
    for key, timestamps in _store.items():
        # Remove old timestamps
        _store[key] = [t for t in timestamps if t > cutoff]
        if not _store[key]:
            expired_keys.append(key)

    for key in expired_keys:
        del _store[key]

    if expired_keys:
        logger.debug("Rate limiter: cleaned up %d expired keys", len(expired_keys))


def check_rate_limit(client_key: str, limit: int) -> tuple[bool, int, int, int]:
    """
    Check if a request is allowed.

    Returns:
        (allowed, limit, remaining, reset_seconds)
    """
    now = time.time()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        _cleanup_expired()

        timestamps = _store.get(client_key, [])
        # Remove expired timestamps
        timestamps = [t for t in timestamps if t > cutoff]

        remaining = max(0, limit - len(timestamps))
        # Time until oldest request in window expires
        reset_seconds = int(WINDOW_SECONDS - (now - timestamps[0])) if timestamps else WINDOW_SECONDS

        if len(timestamps) >= limit:
            _store[client_key] = timestamps
            return False, limit, 0, reset_seconds

        # Allow: record this request
        timestamps.append(now)
        _store[client_key] = timestamps
        remaining = max(0, limit - len(timestamps))

        return True, limit, remaining, reset_seconds


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------


async def rate_limit_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """
    Sliding window rate limiter middleware.

    Runs after auth middleware (needs request.state.auth).
    Skips static assets and dashboard.
    """
    path = request.url.path
    # Skip rate limiting for static assets and dashboard
    if path == "/" or path.startswith("/static") or path == "/favicon.ico":
        return await call_next(request)

    client_key = _get_client_key(request)
    limit = _get_limit(request)

    allowed, max_limit, remaining, reset_seconds = check_rate_limit(client_key, limit)

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded. Please slow down.",
                "retry_after": reset_seconds,
            },
            headers={
                "X-RateLimit-Limit": str(max_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_seconds),
                "Retry-After": str(reset_seconds),
            },
        )

    # Process request
    response = await call_next(request)

    # Add rate limit headers to response
    response.headers["X-RateLimit-Limit"] = str(max_limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_seconds)

    return response


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------


def get_rate_limit_stats() -> dict[str, Any]:
    """Return current rate limiter state (for admin/debug)."""
    with _lock:
        now = time.time()
        cutoff = now - WINDOW_SECONDS
        active = {}
        for key, timestamps in _store.items():
            recent = [t for t in timestamps if t > cutoff]
            if recent:
                active[key] = len(recent)
        return {
            "active_keys": len(active),
            "entries": active,
        }


def reset_rate_limits() -> None:
    """Clear all rate limit state (admin action)."""
    with _lock:
        _store.clear()
    logger.info("Rate limiter: all limits reset")
