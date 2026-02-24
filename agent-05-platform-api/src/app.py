#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Dashboard API

Dual-mode FastAPI server:
  - Database mode — reads from PostgreSQL when available (enables writes)
  - JSON fallback — reads from canonical JSON files when DB is unavailable

Usage:
    uvicorn agent-05-platform-api.src.app:app --reload --port 8000
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db
from .auth import auth_middleware
from .helpers import ROOT, load_all_canonical
from .rate_limiter import rate_limit_middleware
from .routes import audit, export, fhir, health, pharmacies, queue, regulator, verification

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nigeria Pharmacy Registry",
    version="0.3.0",
    description="Dashboard + Verification API for the Nigeria Pharmacy Registry",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth + rate limiting middleware (order matters: auth first, then rate limit)
# Starlette middleware executes in reverse registration order,
# so we register rate_limit first (runs second) then auth (runs first).
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(auth_middleware)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Register route modules
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(pharmacies.router)
app.include_router(verification.router)
app.include_router(queue.router)
app.include_router(audit.router)
app.include_router(fhir.router)
app.include_router(export.router)
app.include_router(regulator.router)

# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    app.state.server_started_at = datetime.now(timezone.utc)
    # Always load JSON (fallback data)
    load_all_canonical()
    # Try to connect to DB (best-effort)
    if db.init_pool():
        logger.info("Running in DATABASE mode")
        _ensure_api_keys_table()
        _ensure_verification_tasks_table()
        _ensure_regulator_staging_tables()
    else:
        logger.info("Running in JSON FALLBACK mode")


@app.on_event("shutdown")
async def shutdown():
    db.close_pool()


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def _ensure_api_keys_table():
    """Run 005_api_keys.sql if the table doesn't exist yet."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'api_keys')"
                )
                exists = cur.fetchone()[0]
                if exists:
                    return

        sql_path = ROOT / "agent-01-data-architecture" / "sql" / "005_api_keys.sql"
        if sql_path.exists():
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_path.read_text())
            logger.info("Applied 005_api_keys.sql migration")
        else:
            logger.warning("005_api_keys.sql not found at %s", sql_path)
    except Exception as e:
        logger.warning("Could not ensure api_keys table: %s", e)


def _ensure_verification_tasks_table():
    """Run 006_verification_tasks.sql if the table doesn't exist yet."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'verification_tasks')"
                )
                exists = cur.fetchone()[0]
                if exists:
                    return

        sql_path = ROOT / "agent-01-data-architecture" / "sql" / "006_verification_tasks.sql"
        if sql_path.exists():
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_path.read_text())
            logger.info("Applied 006_verification_tasks.sql migration")
        else:
            logger.warning("006_verification_tasks.sql not found at %s", sql_path)
    except Exception as e:
        logger.warning("Could not ensure verification_tasks table: %s", e)


def _ensure_regulator_staging_tables():
    """Run 007_regulator_staging.sql if the tables don't exist yet."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'regulator_sync_batches')"
                )
                exists = cur.fetchone()[0]
                if exists:
                    return

        sql_path = ROOT / "agent-01-data-architecture" / "sql" / "007_regulator_staging.sql"
        if sql_path.exists():
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_path.read_text())
            logger.info("Applied 007_regulator_staging.sql migration")
        else:
            logger.warning("007_regulator_staging.sql not found at %s", sql_path)
    except Exception as e:
        logger.warning("Could not ensure regulator_staging tables: %s", e)
