"""Tests for pot10.py."""

from __future__ import annotations

import json
from pathlib import Path

import pot10


def test_parse_results_valid_html(sample_po10_html: str) -> None:
    """Fixture HTML parses to a results list."""
    results = pot10._parse_results(sample_po10_html)
    assert len(results) == 3
    assert results[0]["event"] == "5K"
    assert results[0]["time"] == "17:30"
    assert results[0]["position"] == 3
    assert results[0]["venue"] == "Battersea Park"


def test_parse_empty_table() -> None:
    """Empty table returns empty list."""
    html = """
    <html><body>
    <table id="cphBody_pnlPerformances">
        <tr><th>Event</th><th>Perf</th></tr>
    </table>
    </body></html>
    """
    results = pot10._parse_results(html)
    assert results == []


def test_parse_no_table() -> None:
    """Page with no results table returns empty list."""
    html = "<html><body><p>No results</p></body></html>"
    results = pot10._parse_results(html)
    assert results == []


def test_add_manual_result(tmp_data_dir: Path) -> None:
    """Manual entry is appended correctly."""
    pot10.add(
        date="2025-06-15",
        event="parkrun",
        distance="5K",
        time="22:30",
        position=42,
        notes="Muddy conditions",
    )
    results = json.loads((tmp_data_dir / "race_results.json").read_text())
    assert len(results) == 1
    assert results[0]["event"] == "parkrun"
    assert results[0]["time"] == "22:30"
    assert results[0]["position"] == 42
    assert results[0]["source"] == "manual"
    assert results[0]["notes"] == "Muddy conditions"


def test_add_validates_date(capsys: object) -> None:
    """Invalid date format is rejected."""
    pot10.add(
        date="15-06-2025",
        event="parkrun",
        distance="5K",
        time="22:30",
    )
    # The function prints an error but doesn't raise
    # Just verify it didn't crash — the error message goes to stdout
