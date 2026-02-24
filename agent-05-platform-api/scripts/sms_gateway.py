#!/usr/bin/env python3
"""
Africa's Talking SMS Gateway — CLI tool for sending outbox messages.

Polls the NPR API outbox, sends via Africa's Talking SDK, and marks messages
as sent. Designed to run as a cron job or manual invocation.

Usage:
    # Send pending messages for a campaign
    python3 scripts/sms_gateway.py send --campaign-id <uuid> [--batch-size 100] [--rate-limit 30] [--dry-run]

    # Show campaign status
    python3 scripts/sms_gateway.py status --campaign-id <uuid>

Environment variables:
    AT_USERNAME     Africa's Talking username (default: "sandbox")
    AT_API_KEY      Africa's Talking API key (required for send)
    AT_SENDER_ID    Registered sender ID (optional)
    NPR_API_URL     API base URL (default: http://localhost:8000)
    NPR_API_KEY     Admin API key (required)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

NPR_API_URL = os.environ.get("NPR_API_URL", "http://localhost:8000").rstrip("/")
NPR_API_KEY = os.environ.get("NPR_API_KEY", "")
AT_USERNAME = os.environ.get("AT_USERNAME", "sandbox")
AT_API_KEY = os.environ.get("AT_API_KEY", "")
AT_SENDER_ID = os.environ.get("AT_SENDER_ID", "")


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------


def normalize_nigerian_phone(phone: str) -> str:
    """
    Ensure E.164 format: +234XXXXXXXXXX.

    Handles common Nigerian formats:
      0801... → +234801...
      234801... → +234801...
      +234801... → +234801... (no change)
      08012345678 → +2348012345678
    """
    digits = re.sub(r"[^\d+]", "", phone.strip())

    if digits.startswith("+234"):
        return digits
    if digits.startswith("234") and len(digits) >= 13:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "+234" + digits[1:]

    # Return as-is if we can't normalize (let AT handle validation)
    return digits if digits.startswith("+") else "+" + digits


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def api_headers() -> dict:
    """Standard headers for NPR API requests."""
    return {
        "X-API-Key": NPR_API_KEY,
        "Content-Type": "application/json",
    }


def api_get(path: str, params: dict | None = None) -> dict:
    """GET request to NPR API."""
    url = f"{NPR_API_URL}{path}"
    resp = requests.get(url, headers=api_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str, body: dict) -> dict:
    """POST request to NPR API."""
    url = f"{NPR_API_URL}{path}"
    resp = requests.post(url, headers=api_headers(), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# send subcommand
# ---------------------------------------------------------------------------


def cmd_send(args: argparse.Namespace):
    """Poll outbox → send via AT → mark sent."""
    if not AT_API_KEY and not args.dry_run:
        print("ERROR: AT_API_KEY environment variable is required for sending.", file=sys.stderr)
        sys.exit(1)
    if not NPR_API_KEY:
        print("ERROR: NPR_API_KEY environment variable is required.", file=sys.stderr)
        sys.exit(1)

    # Lazy import AT SDK (only needed for actual sending)
    sms_service = None
    if not args.dry_run:
        try:
            import africastalking
            africastalking.initialize(AT_USERNAME, AT_API_KEY)
            sms_service = africastalking.SMS
        except ImportError:
            print(
                "ERROR: africastalking package not installed. "
                "Run: pip install africastalking",
                file=sys.stderr,
            )
            sys.exit(1)

    campaign_id = args.campaign_id
    batch_size = args.batch_size
    rate_limit = args.rate_limit
    delay = 1.0 / rate_limit if rate_limit > 0 else 0

    total_sent = 0
    total_failed = 0
    total_skipped = 0

    print(f"SMS Gateway — Campaign: {campaign_id}")
    print(f"  API: {NPR_API_URL}")
    print(f"  AT User: {AT_USERNAME}")
    print(f"  Sender ID: {AT_SENDER_ID or '(default)'}")
    print(f"  Batch size: {batch_size}, Rate limit: {rate_limit}/s")
    if args.dry_run:
        print("  *** DRY RUN — no messages will be sent ***")
    print()

    while True:
        # Fetch pending messages from outbox
        try:
            outbox = api_get(
                f"/api/sms/campaigns/{campaign_id}/outbox",
                params={"limit": batch_size},
            )
        except requests.HTTPError as e:
            print(f"ERROR fetching outbox: {e}", file=sys.stderr)
            break

        messages = outbox.get("messages", [])
        if not messages:
            print("Outbox empty — done.")
            break

        print(f"Fetched {len(messages)} pending messages...")

        sent_ids = []
        provider_ids = {}

        for msg in messages:
            msg_id = msg["message_id"]
            phone = normalize_nigerian_phone(msg["phone_number"])
            text = msg["outbound_message"]

            if args.dry_run:
                print(f"  [DRY RUN] Would send to {phone}: {text[:60]}...")
                total_skipped += 1
                continue

            try:
                send_kwargs = {
                    "message": text,
                    "recipients": [phone],
                }
                if AT_SENDER_ID:
                    send_kwargs["sender_id"] = AT_SENDER_ID

                response = sms_service.send(**send_kwargs)

                # Extract provider message ID from AT response
                recipients = response.get("SMSMessageData", {}).get("Recipients", [])
                if recipients:
                    provider_msg_id = recipients[0].get("messageId", "")
                    at_status = recipients[0].get("status", "")
                    at_cost = recipients[0].get("cost", "")
                    print(f"  Sent to {phone} — AT ID: {provider_msg_id}, Status: {at_status}, Cost: {at_cost}")
                    sent_ids.append(msg_id)
                    provider_ids[msg_id] = provider_msg_id
                else:
                    print(f"  WARNING: No recipients in AT response for {phone}")
                    total_failed += 1

            except Exception as e:
                print(f"  FAILED to send to {phone}: {e}", file=sys.stderr)
                total_failed += 1

            # Rate limiting
            if delay > 0:
                time.sleep(delay)

        # In dry-run mode, break after first batch (messages stay pending)
        if args.dry_run:
            break

        # If nothing was sent in this batch, stop to avoid infinite loop
        if not sent_ids:
            print("  No messages sent in this batch — stopping.")
            break

        # Mark sent messages via API
        try:
            result = api_post(
                f"/api/sms/campaigns/{campaign_id}/mark-sent",
                body={"message_ids": sent_ids, "provider_ids": provider_ids},
            )
            marked = result.get("updated", 0)
            total_sent += marked
            print(f"  Marked {marked} messages as sent.")
        except requests.HTTPError as e:
            print(
                f"  WARNING: Failed to mark-sent (messages already dispatched!): {e}",
                file=sys.stderr,
            )
            # Don't re-send — messages are already out
            total_sent += len(sent_ids)

    print()
    print(f"Summary: {total_sent} sent, {total_failed} failed, {total_skipped} skipped")


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace):
    """Show campaign statistics."""
    if not NPR_API_KEY:
        print("ERROR: NPR_API_KEY environment variable is required.", file=sys.stderr)
        sys.exit(1)

    campaign_id = args.campaign_id

    try:
        campaign = api_get(f"/api/sms/campaigns/{campaign_id}")
    except requests.HTTPError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Campaign: {campaign.get('name', campaign_id)}")
    print(f"  Status: {campaign.get('status', 'unknown')}")
    print(f"  Created: {campaign.get('created_at', 'unknown')}")
    print()
    print("Message Counts:")
    print(f"  Total:     {campaign.get('total_messages', 0)}")
    print(f"  Sent:      {campaign.get('sent_count', 0)}")
    print(f"  Delivered: {campaign.get('delivered_count', 0)}")
    print(f"  Replied:   {campaign.get('replied_count', 0)}")
    print(f"  Confirmed: {campaign.get('confirmed_count', 0)}")
    print(f"  Failed:    {campaign.get('failed_count', 0)}")
    print(f"  Expired:   {campaign.get('expired_count', 0)}")

    total = campaign.get("total_messages", 0)
    confirmed = campaign.get("confirmed_count", 0)
    if total > 0:
        print(f"\n  Confirmation rate: {confirmed}/{total} ({100 * confirmed / total:.1f}%)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Africa's Talking SMS Gateway for Nigeria Pharmacy Registry",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # send
    send_parser = subparsers.add_parser("send", help="Send pending outbox messages via AT")
    send_parser.add_argument("--campaign-id", required=True, help="Campaign UUID")
    send_parser.add_argument("--batch-size", type=int, default=100, help="Messages per outbox fetch (default: 100)")
    send_parser.add_argument("--rate-limit", type=float, default=30, help="Max messages per second (default: 30)")
    send_parser.add_argument("--dry-run", action="store_true", help="Fetch outbox but don't send")
    send_parser.set_defaults(func=cmd_send)

    # status
    status_parser = subparsers.add_parser("status", help="Show campaign statistics")
    status_parser.add_argument("--campaign-id", required=True, help="Campaign UUID")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
