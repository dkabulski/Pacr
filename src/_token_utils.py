"""Shared Strava token management — stdlib only, no external deps."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def configure_logging() -> None:
    """Configure root logger: JSON lines if LOG_FORMAT=json, else human-readable."""
    if os.environ.get("LOG_FORMAT") == "json":
        import json as _json

        class _JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return _json.dumps(
                    {
                        "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                        "level": record.levelname,
                        "logger": record.name,
                        "msg": record.getMessage(),
                    }
                )

        _handler = logging.StreamHandler()
        _handler.setFormatter(_JsonFormatter())
        logging.root.addHandler(_handler)
        logging.root.setLevel(logging.INFO)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def read_tokens() -> dict | None:
    """Read tokens from data/tokens.json, falling back to env vars.

    Returns None if neither the file nor the required env vars are present.
    """
    path = DATA_DIR / "tokens.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    # Container / CI fallback: inject token fields via environment variables.
    access = os.environ.get("STRAVA_ACCESS_TOKEN")
    refresh = os.environ.get("STRAVA_REFRESH_TOKEN")
    expires = os.environ.get("STRAVA_TOKEN_EXPIRES_AT")
    if access and refresh and expires:
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": int(expires),
        }
    return None


def write_tokens(tokens: dict) -> None:
    """Write tokens to data/tokens.json, attempting chmod 600."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "tokens.json"
    with open(path, "w") as f:
        json.dump(tokens, f, indent=2)
    # Graceful degradation on filesystems that don't support chmod (Docker volumes).
    with contextlib.suppress(OSError):
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
        raise RuntimeError("No tokens found. Run: uv run strava_auth.py authorize")

    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tokens["access_token"]

    # Token expired — refresh
    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set")

    new_tokens = refresh_access_token(client_id, client_secret, tokens["refresh_token"])
    tokens.update(
        {
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens["refresh_token"],
            "expires_at": new_tokens["expires_at"],
        }
    )
    write_tokens(tokens)
    return tokens["access_token"]
