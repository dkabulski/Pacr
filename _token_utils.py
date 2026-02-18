"""Shared Strava token management — stdlib only, no external deps."""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.request
import urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def read_tokens() -> dict | None:
    """Read tokens from data/tokens.json, return None if missing."""
    path = DATA_DIR / "tokens.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def write_tokens(tokens: dict) -> None:
    """Write tokens to data/tokens.json with chmod 600."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "tokens.json"
    with open(path, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str
) -> dict:
    """POST to Strava to refresh the access token. Returns new token data."""
    url = "https://www.strava.com/oauth/token"
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def get_valid_token() -> str:
    """Return a valid access token, refreshing if expired.

    Reads client credentials from environment variables.
    Raises RuntimeError if tokens are missing or refresh fails.
    """
    tokens = read_tokens()
    if tokens is None:
        raise RuntimeError(
            "No tokens found. Run: uv run strava_auth.py authorize"
        )

    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tokens["access_token"]

    # Token expired — refresh
    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set"
        )

    new_tokens = refresh_access_token(
        client_id, client_secret, tokens["refresh_token"]
    )
    tokens.update(
        {
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens["refresh_token"],
            "expires_at": new_tokens["expires_at"],
        }
    )
    write_tokens(tokens)
    return tokens["access_token"]
