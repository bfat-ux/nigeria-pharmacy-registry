"""
Nigeria Pharmacy Registry — Database Connection Module

Provides a thread-safe connection pool using psycopg2. Configuration
via environment variables with defaults matching docker-compose.yml.

When the database is unavailable, the app gracefully falls back to
serving data from JSON files.

Usage:
    from . import db

    if db.init_pool():
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT count(*) FROM pharmacy_locations")
                print(cur.fetchone()["count"])
"""

from __future__ import annotations

import logging
import os

import psycopg2
from psycopg2 import pool, extras  # noqa: F401 — extras re-exported for callers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env vars with docker-compose defaults)
# ---------------------------------------------------------------------------

DB_CONFIG = {
    "host": os.environ.get("NPR_DB_HOST", "localhost"),
    "port": int(os.environ.get("NPR_DB_PORT", "5432")),
    "dbname": os.environ.get("NPR_DB_NAME", "npr_registry"),
    "user": os.environ.get("NPR_DB_USER", "npr"),
    "password": os.environ.get("NPR_DB_PASSWORD", "npr_local_dev"),
}

_pool: pool.ThreadedConnectionPool | None = None


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


def init_pool(minconn: int = 2, maxconn: int = 10) -> bool:
    """
    Initialize the connection pool.

    Returns True if the database is reachable and the pool is ready.
    Returns False on any failure — the app should fall back to JSON mode.
    """
    global _pool
    try:
        _pool = pool.ThreadedConnectionPool(minconn, maxconn, **DB_CONFIG)
        # Quick connectivity + schema test
        conn = _pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pharmacy_locations")
        count = cur.fetchone()[0]
        cur.close()
        _pool.putconn(conn)
        logger.info(
            "Database pool initialized (%s@%s:%s/%s) — %d pharmacy records in DB",
            DB_CONFIG["user"],
            DB_CONFIG["host"],
            DB_CONFIG["port"],
            DB_CONFIG["dbname"],
            count,
        )
        return True
    except Exception as e:
        logger.warning("Database unavailable, falling back to JSON: %s", e)
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool = None
        return False


def close_pool() -> None:
    """Close all pool connections. Called at app shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("Database pool closed.")


def is_available() -> bool:
    """Check whether the database connection pool is active."""
    return _pool is not None


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------


class get_conn:
    """
    Context manager that checks out a connection from the pool.

    Commits on clean exit, rolls back on exception, always returns the
    connection to the pool.

    Usage::

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT ...")
                rows = cur.fetchall()
    """

    def __enter__(self):
        if _pool is None:
            raise RuntimeError("Database pool not initialized")
        self.conn = _pool.getconn()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.conn.rollback()
        else:
            self.conn.commit()
        _pool.putconn(self.conn)
        return False  # don't suppress exceptions
