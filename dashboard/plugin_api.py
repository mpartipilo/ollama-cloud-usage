"""Ollama Cloud usage — backend API routes.

Mounted at /api/plugins/ollama-cloud-usage/ by the dashboard plugin system.

Thin proxy over the official Ollama Cloud endpoint ``GET https://ollama.com/api/usage``
(authenticated with ``Authorization: Bearer $OLLAMA_API_KEY``). No cookie scraping,
no HTML parsing — the API returns session/weekly quota fractions and per-model
request counts directly.

The API does NOT expose reset timestamps. The weekly reset is DERIVED from the
response's ``activity.period.starting_at`` (a weekday-00:00-UTC boundary); the
session reset is a rolling 5h window with no exposed anchor and is left null.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

USAGE_URL = "https://ollama.com/api/usage"
TIMEOUT = 15

# Reset times are NOT returned by the API, so we derive what we can from data:
#
# WEEKLY: the response's ``activity.period.starting_at`` is the start of the
# rolling billing window, anchored to a weekday 00:00 UTC (Monday for the
# accounts observed). The weekly limit resets on that same weekday boundary, so
# the next reset is ``starting_at + N*7d`` — fully data-driven, no hard-coding.
#
# SESSION: a rolling 5-hour window that resets 5h after the first request in the
# window. The API exposes no timestamp to anchor this, so it can't be derived
# reliably; ``session.resets_at`` is therefore omitted rather than guessed.
WEEK = timedelta(days=7)
SESSION_BLOCK = timedelta(hours=5)


def _load_api_key() -> str:
    """Resolve the Ollama API key: env var first, then ~/.hermes/.env."""
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if key:
        return key

    env_file = Path.home() / ".hermes" / ".env"
    try:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OLLAMA_API_KEY="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        pass
    return ""


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (handles a trailing 'Z' and fractional secs)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_weekly_reset(now: datetime, starting_at: datetime | None) -> datetime | None:
    """Next weekly reset, phased off the billing window's ``starting_at``.

    ``starting_at`` is a weekday 00:00 UTC boundary; the weekly limit resets on
    that same boundary each week, so step 7-day blocks forward until we pass now.
    Returns None if the anchor is missing (so the UI omits the countdown rather
    than showing a guess).
    """
    if starting_at is None:
        return None
    blocks = (now - starting_at) / WEEK
    n = math.floor(blocks) + 1  # first weekly boundary strictly after `now`
    return starting_at + n * WEEK


def _fetch_usage() -> dict:
    key = _load_api_key()
    if not key:
        raise HTTPException(
            status_code=500,
            detail="OLLAMA_API_KEY not found in environment or ~/.hermes/.env",
        )

    req = urllib.request.Request(
        USAGE_URL, headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama API returned HTTP {exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama API unreachable: {exc.reason}",
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="Ollama API returned invalid JSON"
        ) from exc


@router.get("/usage")
async def usage():
    """Return normalized Ollama Cloud usage for the desktop chip/popover."""
    raw = _fetch_usage()

    limits = raw.get("limits", {}) or {}
    session = limits.get("session", {}) or {}
    weekly = limits.get("weekly", {}) or {}
    activity = raw.get("activity", {}) or {}
    period = activity.get("period", {}) or {}

    now = datetime.now(timezone.utc)
    starting_at = _parse_iso(period.get("starting_at", ""))
    weekly_reset = _next_weekly_reset(now, starting_at)

    return {
        "activity": activity,
        "session": {
            "usage": session.get("usage", 0.0),
            "models": session.get("models", []),
            # Rolling 5h window with no API-exposed anchor — no reliable reset.
            "resets_at": None,
        },
        "weekly": {
            "usage": weekly.get("usage", 0.0),
            "models": weekly.get("models", []),
            "resets_at": weekly_reset.isoformat() if weekly_reset else None,
        },
        "fetched_at": now.isoformat(),
        "note": (
            "Weekly reset derived from activity.period.starting_at; session reset "
            "is a rolling 5h window the API does not expose."
        ),
    }
