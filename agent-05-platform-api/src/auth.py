"""
Nigeria Pharmacy Registry — API Key Authentication

Provides:
    - API key validation via X-API-Key header (bcrypt-hashed keys in PostgreSQL)
    - Tier-based access control (public, registry_read, registry_write, admin)
    - Scope-checking FastAPI dependencies
    - Contact data redaction for public-tier callers
    - In-memory key cache (5 min TTL) to avoid bcrypt on every request

Key format: npr_{env}_{32 alphanumeric}
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import bcrypt
from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from . import db
from .db import extras

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth context — attached to request.state.auth
# ---------------------------------------------------------------------------

TIER_HIERARCHY = {
    "public": 0,
    "registry_read": 1,
    "registry_write": 2,
    "admin": 3,
}

# Default scopes per tier
DEFAULT_SCOPES = {
    "public": ["read:pharmacies", "read:stats"],
    "registry_read": ["read:pharmacies", "read:stats", "read:contacts", "read:history"],
    "registry_write": [
        "read:pharmacies", "read:stats", "read:contacts", "read:history",
        "write:verify",
    ],
    "admin": [
        "read:pharmacies", "read:stats", "read:contacts", "read:history",
        "write:verify", "admin:keys", "admin:audit",
    ],
}


@dataclass
class AuthContext:
    """Resolved authentication context for a request."""

    tier: str = "public"
    scopes: list[str] = field(default_factory=lambda: DEFAULT_SCOPES["public"][:])
    actor_id: str = "anonymous"
    actor_type: str = "anonymous"
    key_id: str | None = None


ANONYMOUS = AuthContext()

# ---------------------------------------------------------------------------
# Key cache — avoids bcrypt verification on every request
# ---------------------------------------------------------------------------

_KEY_CACHE: dict[str, tuple[AuthContext, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(api_key: str) -> AuthContext | None:
    """Return cached AuthContext if still valid, else None."""
    entry = _KEY_CACHE.get(api_key)
    if entry is None:
        return None
    ctx, cached_at = entry
    if time.time() - cached_at > _CACHE_TTL:
        del _KEY_CACHE[api_key]
        return None
    return ctx


def _cache_set(api_key: str, ctx: AuthContext) -> None:
    _KEY_CACHE[api_key] = (ctx, time.time())


def clear_cache() -> None:
    """Clear the entire key cache (useful after key revocation)."""
    _KEY_CACHE.clear()


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------


def _validate_key(api_key: str) -> AuthContext | None:
    """
    Validate an API key against the database.

    Returns AuthContext on success, None if key is invalid/expired/inactive.
    """
    if not db.is_available():
        logger.warning("Auth: DB unavailable, cannot validate API key")
        return None

    prefix = api_key[:16] if len(api_key) >= 16 else api_key

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, key_hash, tier, scopes, expires_at, is_active
                    FROM api_keys
                    WHERE key_prefix = %s AND is_active = true
                    """,
                    (prefix,),
                )
                rows = cur.fetchall()

        if not rows:
            return None

        # Check bcrypt hash against each candidate (usually just one)
        key_bytes = api_key.encode("utf-8")
        for row in rows:
            try:
                if bcrypt.checkpw(key_bytes, row["key_hash"].encode("utf-8")):
                    # Check expiry
                    if row["expires_at"] is not None:
                        from datetime import datetime, timezone

                        if row["expires_at"] < datetime.now(timezone.utc):
                            logger.info("Auth: Key %s... expired", prefix[:8])
                            return None

                    tier = row["tier"]
                    scopes = list(row["scopes"]) if row["scopes"] else DEFAULT_SCOPES.get(tier, [])
                    if not scopes:
                        scopes = DEFAULT_SCOPES.get(tier, [])

                    ctx = AuthContext(
                        tier=tier,
                        scopes=scopes,
                        actor_id=f"apikey:{row['id']}",
                        actor_type="api_user",
                        key_id=str(row["id"]),
                    )

                    # Update last_used_at (fire-and-forget, don't block the request)
                    try:
                        with db.get_conn() as conn2:
                            with conn2.cursor() as cur2:
                                cur2.execute(
                                    "UPDATE api_keys SET last_used_at = now() WHERE id = %s",
                                    (row["id"],),
                                )
                    except Exception:
                        pass  # non-critical

                    return ctx
            except Exception as e:
                logger.debug("Auth: bcrypt check failed for row %s: %s", row["id"], e)
                continue

        return None

    except Exception as e:
        logger.error("Auth: Key validation error: %s", e)
        return None


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------


async def auth_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """
    Resolve the caller's identity from X-API-Key header.

    - No header / empty header -> public (anonymous) tier
    - Valid key -> authenticated tier with scopes
    - Invalid key -> 401
    """
    # Skip auth for static assets and dashboard
    path = request.url.path
    if path == "/" or path.startswith("/static") or path == "/favicon.ico":
        request.state.auth = ANONYMOUS
        return await call_next(request)

    api_key = request.headers.get("X-API-Key", "").strip()

    if not api_key:
        # Anonymous / public tier
        request.state.auth = ANONYMOUS
        return await call_next(request)

    # Check cache first
    ctx = _cache_get(api_key)
    if ctx is not None:
        request.state.auth = ctx
        return await call_next(request)

    # Validate against DB
    ctx = _validate_key(api_key)
    if ctx is None:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired API key"},
            headers={"WWW-Authenticate": "ApiKey"},
        )

    _cache_set(api_key, ctx)
    request.state.auth = ctx
    return await call_next(request)


# ---------------------------------------------------------------------------
# Scope-checking dependencies
# ---------------------------------------------------------------------------


def require_tier(min_tier: str) -> Callable:
    """
    FastAPI dependency that checks the caller meets the minimum tier.

    Usage:
        @app.post("/api/endpoint", dependencies=[Depends(require_tier("registry_write"))])
    """
    min_level = TIER_HIERARCHY.get(min_tier, 0)

    async def _check(request: Request):
        auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
        caller_level = TIER_HIERARCHY.get(auth.tier, 0)
        if caller_level < min_level:
            from fastapi import HTTPException

            if auth.tier == "public" and auth.actor_type == "anonymous":
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required. Provide an API key via the X-API-Key header.",
                )
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required tier: {min_tier}, your tier: {auth.tier}",
            )

    return _check


def require_scope(scope: str) -> Callable:
    """
    FastAPI dependency that checks the caller has a specific scope.

    Usage:
        @app.get("/api/endpoint", dependencies=[Depends(require_scope("read:contacts"))])
    """

    async def _check(request: Request):
        auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
        if scope not in auth.scopes:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: {scope}",
            )

    return _check


# ---------------------------------------------------------------------------
# Contact redaction for public tier
# ---------------------------------------------------------------------------


def redact_phone(phone: str | None) -> str | None:
    """Mask phone number: +234****1234 (preserve last 4)."""
    if not phone:
        return None
    if len(phone) <= 4:
        return "****"
    return phone[:4] + "****" + phone[-4:]


def redact_email(email: str | None) -> str | None:
    """Mask email: u***@example.com (first char + domain)."""
    if not email:
        return None
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return local[0] + "***@" + domain if local else "***@" + domain


def redact_contacts_in_response(data: Any, auth: AuthContext) -> Any:
    """
    Redact contact data in API response if caller is public tier.

    Works on dicts and lists of dicts. Modifies in-place and returns.
    """
    if auth.tier != "public":
        return data

    if isinstance(data, dict):
        _redact_dict(data)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _redact_dict(item)

    return data


def _redact_dict(d: dict) -> None:
    """Redact contact fields in a single dict."""
    if "phone" in d:
        d["phone"] = redact_phone(d.get("phone"))
    if "email" in d:
        d["email"] = redact_email(d.get("email"))
    if "contact_person" in d:
        d.pop("contact_person", None)

    # Handle nested contacts list
    if "contacts" in d and isinstance(d["contacts"], list):
        redacted = []
        for c in d["contacts"]:
            if isinstance(c, dict):
                rc = dict(c)
                if rc.get("type") == "phone":
                    rc["value"] = redact_phone(rc.get("value"))
                elif rc.get("type") == "email":
                    rc["value"] = redact_email(rc.get("value"))
                rc.pop("person", None)
                redacted.append(rc)
        d["contacts"] = redacted

    # Handle GeoJSON properties
    if "properties" in d and isinstance(d["properties"], dict):
        props = d["properties"]
        if "phone" in props:
            props["phone"] = redact_phone(props.get("phone"))
        if "email" in props:
            props["email"] = redact_email(props.get("email"))
        props.pop("contact_person", None)
