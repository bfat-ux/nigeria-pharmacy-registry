#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — API Key Management CLI

Usage:
    python3.13 agent-05-platform-api/scripts/manage_keys.py create \
        --name "Field Agent App" --tier registry_write --email "ops@npr.ng"

    python3.13 agent-05-platform-api/scripts/manage_keys.py list

    python3.13 agent-05-platform-api/scripts/manage_keys.py revoke --key-id <uuid>

Key format: npr_{env}_{32 alphanumeric}
Keys are bcrypt-hashed before storage. The plaintext key is shown ONCE at creation.
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from datetime import datetime, timezone

import bcrypt
import psycopg2
from psycopg2 import extras

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALID_TIERS = ("public", "registry_read", "registry_write", "admin")

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


def get_db_config() -> dict:
    return {
        "host": os.environ.get("NPR_DB_HOST", "localhost"),
        "port": int(os.environ.get("NPR_DB_PORT", "5432")),
        "dbname": os.environ.get("NPR_DB_NAME", "npr_registry"),
        "user": os.environ.get("NPR_DB_USER", "npr"),
        "password": os.environ.get("NPR_DB_PASSWORD", "npr_local_dev"),
    }


def get_connection():
    config = get_db_config()
    conn = psycopg2.connect(**config)
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_api_key(env: str = "live") -> str:
    """Generate a key: npr_{env}_{32 alphanumeric}."""
    charset = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(charset) for _ in range(32))
    return f"npr_{env}_{random_part}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_create(args):
    """Create a new API key."""
    tier = args.tier
    if tier not in VALID_TIERS:
        print(f"Error: Invalid tier '{tier}'. Valid tiers: {VALID_TIERS}")
        sys.exit(1)

    env = os.environ.get("NPR_ENV", "live")
    plaintext_key = generate_api_key(env)
    prefix = plaintext_key[:16]

    # Hash with bcrypt
    key_hash = bcrypt.hashpw(
        plaintext_key.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    scopes = DEFAULT_SCOPES.get(tier, [])
    created_by = args.created_by or f"cli:{os.environ.get('USER', 'unknown')}"

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO api_keys (
                    key_prefix, key_hash, name, tier, scopes,
                    owner_email, owner_org, created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    prefix,
                    key_hash,
                    args.name,
                    tier,
                    scopes,
                    args.email,
                    args.org,
                    created_by,
                ),
            )
            row = cur.fetchone()

        print()
        print("=" * 60)
        print("  API KEY CREATED SUCCESSFULLY")
        print("=" * 60)
        print()
        print(f"  Key ID:      {row['id']}")
        print(f"  Name:        {args.name}")
        print(f"  Tier:        {tier}")
        print(f"  Scopes:      {', '.join(scopes)}")
        print(f"  Owner:       {args.email}")
        if args.org:
            print(f"  Org:         {args.org}")
        print(f"  Created:     {row['created_at']}")
        print()
        print("  PLAINTEXT KEY (shown ONCE, save it now):")
        print()
        print(f"  {plaintext_key}")
        print()
        print("=" * 60)
        print()
        print("  Usage:")
        print(f'  curl -H "X-API-Key: {plaintext_key}" http://localhost:8000/api/pharmacies')
        print()

    finally:
        conn.close()


def cmd_list(args):
    """List all API keys (active and inactive)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, key_prefix, name, tier, scopes,
                       owner_email, owner_org, is_active,
                       expires_at, last_used_at, created_at, created_by
                FROM api_keys
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()

        if not rows:
            print("\nNo API keys found.\n")
            return

        print()
        print(f"{'ID':<38} {'Name':<25} {'Tier':<18} {'Active':<8} {'Last Used':<22} {'Owner'}")
        print("-" * 140)

        for r in rows:
            last_used = str(r["last_used_at"])[:19] if r["last_used_at"] else "never"
            active = "yes" if r["is_active"] else "NO"
            print(
                f"{r['id']!s:<38} {r['name']:<25} {r['tier']:<18} {active:<8} {last_used:<22} {r['owner_email']}"
            )

        print()
        print(f"Total: {len(rows)} keys ({sum(1 for r in rows if r['is_active'])} active)")
        print()

    finally:
        conn.close()


def cmd_revoke(args):
    """Revoke (deactivate) an API key."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, tier, is_active FROM api_keys WHERE id = %s",
                (args.key_id,),
            )
            row = cur.fetchone()

            if not row:
                print(f"\nError: Key '{args.key_id}' not found.\n")
                sys.exit(1)

            if not row["is_active"]:
                print(f"\nKey '{row['name']}' is already revoked.\n")
                return

            cur.execute(
                "UPDATE api_keys SET is_active = false WHERE id = %s",
                (args.key_id,),
            )

        print(f"\nRevoked key: {row['name']} ({row['tier']}) — ID: {row['id']}\n")

    finally:
        conn.close()


def cmd_info(args):
    """Show details for a specific API key."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, key_prefix, name, tier, scopes,
                       owner_email, owner_org, is_active,
                       rate_limit_override, expires_at, last_used_at,
                       created_at, created_by
                FROM api_keys
                WHERE id = %s
                """,
                (args.key_id,),
            )
            row = cur.fetchone()

        if not row:
            print(f"\nError: Key '{args.key_id}' not found.\n")
            sys.exit(1)

        print()
        print(f"  Key ID:         {row['id']}")
        print(f"  Prefix:         {row['key_prefix']}...")
        print(f"  Name:           {row['name']}")
        print(f"  Tier:           {row['tier']}")
        print(f"  Scopes:         {', '.join(row['scopes']) if row['scopes'] else 'none'}")
        print(f"  Owner Email:    {row['owner_email']}")
        print(f"  Owner Org:      {row['owner_org'] or '-'}")
        print(f"  Active:         {row['is_active']}")
        print(f"  Rate Override:  {row['rate_limit_override'] or 'default'}")
        print(f"  Expires:        {row['expires_at'] or 'never'}")
        print(f"  Last Used:      {row['last_used_at'] or 'never'}")
        print(f"  Created:        {row['created_at']}")
        print(f"  Created By:     {row['created_by']}")
        print()

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Nigeria Pharmacy Registry — API Key Management",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create
    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--name", required=True, help="Human-readable name for the key")
    create_parser.add_argument(
        "--tier",
        required=True,
        choices=VALID_TIERS,
        help="Access tier",
    )
    create_parser.add_argument("--email", required=True, help="Owner email address")
    create_parser.add_argument("--org", default=None, help="Owner organization")
    create_parser.add_argument("--created-by", default=None, help="Who is creating this key")

    # list
    subparsers.add_parser("list", help="List all API keys")

    # revoke
    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key")
    revoke_parser.add_argument("--key-id", required=True, help="UUID of the key to revoke")

    # info
    info_parser = subparsers.add_parser("info", help="Show details for a key")
    info_parser.add_argument("--key-id", required=True, help="UUID of the key")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": cmd_create,
        "list": cmd_list,
        "revoke": cmd_revoke,
        "info": cmd_info,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
