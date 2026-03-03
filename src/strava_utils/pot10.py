# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests>=2.32",
#     "beautifulsoup4>=4.12",
#     "fire>=0.7",
# ]
# ///
"""Power of 10 race results by athlete ID.

⚠ EXPERIMENTAL — Power of 10 website is currently being rebuilt.
Web scraping may fail. Use manual `add` as the primary workflow.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire
import requests
from bs4 import BeautifulSoup

import _token_utils


def _results_path() -> Path:
    return _token_utils.DATA_DIR / "race_results.json"


def _raw_html_path() -> Path:
    return _token_utils.DATA_DIR / "po10_raw.html"


BASE_URL = "https://www.thepowerof10.info/athletes/profile.aspx"


def _parse_results(html: str, verbose: bool = False) -> list[dict]:
    """Parse race results from Power of 10 profile HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Find results table — it typically has class "results" or is the main data table
    table = soup.find("table", id="cphBody_pnlPerformances")
    if table is None:
        # Fallback: look for any table with performance data
        tables = soup.find_all("table")
        for t in tables:
            headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if "event" in headers and "perf" in headers:
                table = t
                break

    if table is None:
        if verbose:
            all_tables = soup.find_all("table")
            n = len(all_tables)
            print(f"Warning: No results table found. Page has {n} tables.")
            for i, t in enumerate(all_tables[:5]):
                headers = [th.get_text(strip=True) for th in t.find_all("th")]
                print(f"  Table {i}: headers={headers[:6]}")
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    # Get header indices
    header_row = rows[0]
    headers = [
        th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])
    ]

    if verbose:
        print(f"Headers found: {headers}")

    # Map expected columns
    col_map: dict[str, int] = {}
    for i, h in enumerate(headers):
        if "event" in h:
            col_map["event"] = i
        elif h in ("perf", "performance", "time"):
            col_map["perf"] = i
        elif "date" in h:
            col_map["date"] = i
        elif h in ("pos", "position"):
            col_map["pos"] = i
        elif "venue" in h or "meeting" in h:
            col_map["venue"] = i

    results: list[dict] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        cell_texts = [c.get_text(strip=True) for c in cells]

        result: dict = {"source": "power_of_10"}
        if "event" in col_map and col_map["event"] < len(cell_texts):
            result["event"] = cell_texts[col_map["event"]]
        if "perf" in col_map and col_map["perf"] < len(cell_texts):
            result["time"] = cell_texts[col_map["perf"]]
        if "date" in col_map and col_map["date"] < len(cell_texts):
            result["date"] = cell_texts[col_map["date"]]
        if "pos" in col_map and col_map["pos"] < len(cell_texts):
            pos_text = cell_texts[col_map["pos"]]
            if pos_text.isdigit():
                result["position"] = int(pos_text)
        if "venue" in col_map and col_map["venue"] < len(cell_texts):
            result["venue"] = cell_texts[col_map["venue"]]

        # Only include if we got at least event and time
        if "event" in result and "time" in result:
            results.append(result)

    return results


def _load_results() -> list[dict]:
    """Load cached results from disk."""
    if not _results_path().exists():
        return []
    with open(_results_path()) as f:
        return json.load(f)


def _save_results(results: list[dict]) -> None:
    """Save results to disk."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_results_path(), "w") as f:
        json.dump(results, f, indent=2)


def fetch(athlete_id: int, verbose: bool = False) -> None:
    """Fetch race results from Power of 10 by athlete ID.

    ⚠ EXPERIMENTAL: The Power of 10 website is being rebuilt. Web scraping
    may fail or return incomplete results. Use `add` for reliable manual entry.
    """
    print(
        "⚠  Warning: Power of 10 web fetch is experimental — "
        "the site is being rebuilt and scraping may fail.\n"
        "   Use `pot10.py add` for reliable manual entry.\n"
    )
    url = f"{BASE_URL}?athleteid={athlete_id}"
    print(f"Fetching: {url}")

    resp = requests.get(url, timeout=30, allow_redirects=True)

    if verbose:
        print(f"HTTP status: {resp.status_code}")
        print(f"Final URL: {resp.url}")

    if resp.status_code != 200:
        print(f"Error: HTTP {resp.status_code}")
        return

    # Detect redirect to myathletics.uk
    if "myathletics.uk" in resp.url:
        print(f"Warning: Redirected to myathletics.uk ({resp.url})")
        print("Power of 10 may have migrated this athlete's data.")
        return

    html = resp.text

    if verbose:
        _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_raw_html_path(), "w") as f:
            f.write(html)
        print(f"Raw HTML saved to {_raw_html_path()}")

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        print(f"Page contains {len(tables)} tables")

    # Check for reCAPTCHA
    if "recaptcha" in html.lower() or "captcha" in html.lower():
        print("Warning: Page contains CAPTCHA. Results may be incomplete.")
        print("Use 'pot10.py add' to manually enter results.")

    results = _parse_results(html, verbose=verbose)

    if not results:
        print("No results found. The page structure may have changed.")
        print(
            "Use --verbose to inspect the HTML, or use 'pot10.py add' for manual entry."
        )
        return

    _save_results(results)
    print(f"Saved {len(results)} results to {_results_path()}")

    for r in results[:10]:
        date = r.get("date", "?")
        event = r.get("event", "?")
        time_ = r.get("time", "?")
        pos = r.get("position", "")
        pos_str = f"  #{pos}" if pos else ""
        print(f"  {date}  {event:<15}  {time_}{pos_str}")

    if len(results) > 10:
        print(f"  ... and {len(results) - 10} more")


def show() -> None:
    """Display cached race results."""
    results = _load_results()
    if not results:
        print("No race results cached. Run: uv run pot10.py fetch --athlete_id=<id>")
        return

    for r in results:
        date = r.get("date", "?")
        event = r.get("event", "?")
        time_ = r.get("time", "?")
        source = r.get("source", "?")
        pos = r.get("position", "")
        pos_str = f"  #{pos}" if pos else ""
        print(f"  {date}  {event:<15}  {time_}{pos_str}  [{source}]")


def add(
    date: str,
    event: str,
    distance: str,
    time: str,
    position: int | None = None,
    notes: str = "",
) -> None:
    """Manually add a race result."""
    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format '{date}'. Use YYYY-MM-DD.")
        return

    result: dict = {
        "date": date,
        "event": event,
        "distance": distance,
        "time": time,
        "source": "manual",
    }
    if position is not None:
        result["position"] = position
    if notes:
        result["notes"] = notes

    results = _load_results()
    results.append(result)
    _save_results(results)
    print(f"Added: {date} {event} {distance} {time}")


if __name__ == "__main__":
    fire.Fire({"fetch": fetch, "show": show, "add": add})
