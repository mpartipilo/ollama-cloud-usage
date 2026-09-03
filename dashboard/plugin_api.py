"""Ollama Cloud usage — backend API routes.

Mounted at /api/plugins/ollama-cloud-usage/ by the dashboard plugin system.

Thin proxy over the official Ollama Cloud endpoint ``GET https://ollama.com/api/usage``
(authenticated with ``Authorization: Bearer $OLLAMA_API_KEY``). No cookie scraping,
no HTML parsing — the API returns the monthly usage fraction and per-model request
counts directly.

Since Sept 2026 Ollama reports a single ``limits.monthly`` window (monthly usage
credits under the per-token billing model) instead of the old ``session``/``weekly``
GPU-time quotas.

Reset time: the monthly usage resets on your plan's *subscription anniversary* (the
same day-of-month your plan started), which this endpoint does NOT expose. The only
time data in the payload is ``activity.period`` — a rolling ``last_4_weeks`` window
unrelated to the anniversary. So ``monthly.resets_at`` is omitted/None rather than
guessed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

router = APIRouter()

USAGE_URL = "https://ollama.com/api/usage"
TIMEOUT = 15


def _load_api_key() -> str:
    """Resolve the Ollama API key: env var first, then ~/.hermes/.env."""
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if key:
        return key

    env_file = os.path.expanduser("~/.hermes/.env")
    try:
        if os.path.exists(env_file):
            with open(env_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("OLLAMA_API_KEY="):
                        value = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if value:
                            return value
    except OSError:
        pass
    return ""


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
    """Return normalized Ollama Cloud usage for the desktop chip/popover.

    ``usage`` is a 0-1 fraction of the monthly allowance; ``models`` is the
    per-model request count for the current month. ``resets_at`` is always null
    because the resets on your plan's subscription anniversary, which the API
    does not expose (``activity.period`` is a rolling 4-week window, unrelated).
    """
    raw = _fetch_usage()

    limits = raw.get("limits", {}) or {}
    monthly = limits.get("monthly", {}) or {}
    activity = raw.get("activity", {}) or {}
    period = activity.get("period", {}) or {}

    return {
        "activity": activity,
        "monthly": {
            "usage": monthly.get("usage", 0.0),
            "models": monthly.get("models", []),
            # Resets on the plan's subscription anniversary (day-of-month your
            # plan started) — monthly reset date is not in the API payload, so
            # omit the countdown rather than show a guess.
            "resets_at": None,
            "period": period,
        },
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Ollama measures usage monthly (usage credits). It resets on your "
            "plan's subscription anniversary, which the /api/usage endpoint "
            "does not expose — no auto countdown."
        ),
    }
