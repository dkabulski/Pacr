# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests>=2.32",
#     "fire>=0.7",
#     "python-dotenv>=1.0",
# ]
# ///
"""Strava OAuth setup — one-time authorisation flow."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import fire
import requests
from dotenv import load_dotenv

import _token_utils

load_dotenv()

_auth_code: str | None = None
_server_ready = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handle the OAuth callback from Strava."""

    def do_GET(self) -> None:  # noqa: N802
        global _auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorisation successful!</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<h1>Authorisation failed: {error}</h1>".encode()
            )

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default request logging."""


def authorize() -> None:
    """Start OAuth flow, exchange code for tokens, save to data/tokens.json."""
    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    redirect_uri = os.environ.get(
        "STRAVA_REDIRECT_URI", "http://localhost:8000/callback"
    )

    if not client_id or not client_secret:
        print("Error: STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set.")
        print("Copy .env.example to .env and fill in your credentials.")
        return

    # Build authorisation URL
    auth_url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=read,activity:read_all"
    )

    # Start local callback server
    server = HTTPServer(("localhost", 8000), _CallbackHandler)

    def run_server() -> None:
        _server_ready.set()
        server.handle_request()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    _server_ready.wait()

    print(f"Opening browser for Strava authorisation...\n{auth_url}\n")
    webbrowser.open(auth_url)
    print("Waiting for callback...")
    thread.join(timeout=120)
    server.server_close()

    if not _auth_code:
        print("Error: No authorisation code received. Timed out or denied.")
        return

    # Exchange code for tokens
    print("Exchanging authorisation code for tokens...")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _auth_code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"Error: Token exchange failed (HTTP {resp.status_code})")
        print(resp.text)
        return

    data = resp.json()

    if "errors" in data:
        print(f"Error: Strava returned errors: {data['errors']}")
        return

    # Save tokens
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
        "token_type": data.get("token_type", "Bearer"),
    }
    _token_utils.write_tokens(tokens)
    print("Tokens saved to data/tokens.json")

    # Save athlete profile
    if "athlete" in data:
        athlete_path = _token_utils.DATA_DIR / "athlete.json"
        with open(athlete_path, "w") as f:
            json.dump(data["athlete"], f, indent=2)
        print(f"Athlete profile saved to {athlete_path}")


def status() -> None:
    """Check token validity, attempt refresh if expired."""
    tokens = _token_utils.read_tokens()
    if tokens is None:
        print("No tokens found. Run: uv run strava_auth.py authorize")
        return

    import time

    expires_at = tokens.get("expires_at", 0)
    now = time.time()

    if now < expires_at - 60:
        remaining = int((expires_at - now) / 60)
        print(f"Token valid — expires in {remaining} minutes")
    else:
        print("Token expired — attempting refresh...")
        try:
            token = _token_utils.get_valid_token()
            print(f"Token refreshed successfully: {token[:8]}...")
        except RuntimeError as e:
            print(f"Refresh failed: {e}")


if __name__ == "__main__":
    fire.Fire({"authorize": authorize, "status": status})
