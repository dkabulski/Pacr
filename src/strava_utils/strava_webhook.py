# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "fire>=0.7",
#     "python-dotenv>=1.0",
#     "requests>=2.32",
# ]
# ///
"""Strava webhook management — subscribe, verify, and receive push events.

Usage:
    uv run src/strava_utils/strava_webhook.py subscribe <callback_url>
    uv run src/strava_utils/strava_webhook.py unsubscribe <subscription_id>
    uv run src/strava_utils/strava_webhook.py status
    uv run src/strava_utils/strava_webhook.py serve [--port=8001]

The *serve* command starts a local HTTP server that:
  - Responds to Strava's GET verification challenge
  - Writes incoming push events to data/webhook_events.json

The bot processes pending events whenever STRAVA_WEBHOOK_PORT is set and the
background webhook-event checker job runs (every 60 s).  Subscribe Strava to
your public callback URL first; use a reverse proxy or ngrok in development.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire
from dotenv import load_dotenv

load_dotenv()

import _token_utils  # noqa: E402

_token_utils.configure_logging()
logger = logging.getLogger("pacr")

_STRAVA_SUBS_URL = "https://www.strava.com/api/v3/push_subscriptions"
_EVENTS_FILE = _token_utils.DATA_DIR / "webhook_events.json"


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def subscribe(callback_url: str, verify_token: str = "pacr_verify") -> None:
    """Subscribe to Strava webhook events.

    Args:
        callback_url: Publicly reachable HTTPS URL that Strava will POST to.
        verify_token: Secret string Strava echoes back during verification.
    """
    import requests

    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit(
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env"
        )

    resp = requests.post(
        _STRAVA_SUBS_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "callback_url": callback_url,
            "verify_token": verify_token,
        },
        timeout=15,
    )
    print(json.dumps(resp.json(), indent=2))


def unsubscribe(subscription_id: int) -> None:
    """Cancel a Strava webhook subscription by ID."""
    import requests

    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    resp = requests.delete(
        f"{_STRAVA_SUBS_URL}/{subscription_id}",
        params={"client_id": client_id, "client_secret": client_secret},
        timeout=15,
    )
    print(resp.status_code, resp.text)


def status() -> None:
    """List current Strava webhook subscriptions."""
    import requests

    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    resp = requests.get(
        _STRAVA_SUBS_URL,
        params={"client_id": client_id, "client_secret": client_secret},
        timeout=15,
    )
    print(json.dumps(resp.json(), indent=2))


def serve(port: int = 8001, verify_token: str = "pacr_verify") -> None:
    """Run a local webhook receiver on the given port.

    Strava sends a GET to verify the endpoint, then POSTs events as JSON.
    Events are appended to data/webhook_events.json and picked up by the bot's
    background job (runs every 60 s when STRAVA_WEBHOOK_PORT is set).
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            mode = params.get("hub.mode", [""])[0]
            token = params.get("hub.verify_token", [""])[0]
            challenge = params.get("hub.challenge", [""])[0]
            if mode == "subscribe" and token == verify_token:
                body = json.dumps({"hub.challenge": challenge}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                logger.info("Webhook verified by Strava (challenge=%s)", challenge)
            else:
                self.send_response(403)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                event = json.loads(body)
                _append_event(event)
                logger.info(
                    "Webhook event received: type=%s id=%s aspect=%s",
                    event.get("object_type"),
                    event.get("object_id"),
                    event.get("aspect_type"),
                )
                self.send_response(200)
                self.end_headers()
            except Exception as exc:
                logger.error("Webhook POST error: %s", exc)
                self.send_response(500)
                self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
            logger.debug("Webhook HTTP: " + fmt, *args)

    server = HTTPServer(("", port), _Handler)
    logger.info("Webhook server listening on port %d", port)
    server.serve_forever()


# ---------------------------------------------------------------------------
# File-based event store (shared with the bot process)
# ---------------------------------------------------------------------------


def _append_event(event: dict) -> None:
    """Append a Strava push event to the pending events file."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    if _EVENTS_FILE.exists():
        try:
            events = json.loads(_EVENTS_FILE.read_text())
        except Exception:
            pass
    events.append(event)
    _EVENTS_FILE.write_text(json.dumps(events, indent=2))


def _pop_events() -> list[dict]:
    """Read and atomically clear all pending webhook events.

    Returns an empty list if no events are queued.
    """
    if not _EVENTS_FILE.exists():
        return []
    try:
        events: list[dict] = json.loads(_EVENTS_FILE.read_text())
        _EVENTS_FILE.unlink()
        return events
    except Exception:
        return []


if __name__ == "__main__":
    fire.Fire(
        {
            "subscribe": subscribe,
            "unsubscribe": unsubscribe,
            "status": status,
            "serve": serve,
        }
    )
