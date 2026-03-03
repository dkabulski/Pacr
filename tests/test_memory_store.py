"""Tests for the ChromaDB-backed memory store (src/memory/store.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _require_chromadb() -> None:
    """Skip all tests in this module if chromadb is not installed."""
    pytest.importorskip("chromadb")


def test_query_memories_empty(tmp_data_dir: Path) -> None:
    """Fresh collection should return an empty list."""
    from memory.store import query_memories

    result = query_memories("how did my tempo session feel?")
    assert result == []


def test_save_memory_returns_true(tmp_data_dir: Path) -> None:
    """Saving a valid memory should return True."""
    from memory.store import save_memory

    ok = save_memory(
        "Easy 10km run felt comfortable, HR well controlled.",
        {"category": "session_feedback", "date": "2026-03-02"},
    )
    assert ok is True


def test_save_and_query_roundtrip(tmp_data_dir: Path) -> None:
    """A saved memory should appear in query results."""
    from memory.store import query_memories, save_memory

    note = "Tempo session on 2026-03-02: streets were busy, legs felt heavy."
    save_memory(note, {"category": "session_feedback", "date": "2026-03-02"})

    results = query_memories("tempo session felt hard")
    assert len(results) >= 1
    texts = [r["text"] for r in results]
    assert note in texts
    # Each result must have the expected keys
    for r in results:
        assert "text" in r
        assert "metadata" in r
        assert "distance" in r


def test_query_n_results_exceeds_count(tmp_data_dir: Path) -> None:
    """Querying with n_results > stored count should not raise."""
    from memory.store import query_memories, save_memory

    save_memory("First note.", {"category": "general", "date": "2026-03-01"})
    save_memory("Second note.", {"category": "general", "date": "2026-03-02"})

    results = query_memories("note", n_results=10)
    assert len(results) == 2  # only 2 stored


def test_save_memory_graceful_failure(tmp_data_dir: Path) -> None:
    """save_memory should return False when ChromaDB is unavailable."""
    from memory import store

    with patch.object(store, "_get_collection", return_value=None):
        ok = store.save_memory(
            "Should not save.", {"category": "general", "date": "2026-03-02"}
        )
    assert ok is False


def test_query_memories_graceful_failure(tmp_data_dir: Path) -> None:
    """query_memories should return [] when ChromaDB is unavailable."""
    from memory import store

    with patch.object(store, "_get_collection", return_value=None):
        result = store.query_memories("anything")
    assert result == []


def test_index_activities_roundtrip(tmp_data_dir: Path) -> None:
    """Indexed activities should be retrievable by semantic query."""
    from memory.store import index_activities, query_memories

    acts = [
        {
            "id": 1001,
            "name": "Brutal track intervals",
            "date": "2026-03-01T07:00:00Z",
            "distance_km": 8.0,
            "pace": "4:10",
            "avg_hr": 172.0,
            "elevation_m": 5.0,
            "type": "Run",
        },
        {
            "id": 1002,
            "name": "Easy recovery jog",
            "date": "2026-03-02T08:00:00Z",
            "distance_km": 6.0,
            "pace": "5:45",
            "avg_hr": 128.0,
            "elevation_m": None,
            "type": "Run",
        },
    ]
    count = index_activities(acts)
    assert count == 2

    results = query_memories("hard interval session")
    texts = [r["text"] for r in results]
    assert any("Brutal track intervals" in t for t in texts)


def test_index_activities_idempotent(tmp_data_dir: Path) -> None:
    """Re-indexing the same activities should not raise or create duplicates."""
    from memory.store import index_activities, query_memories

    act = [
        {
            "id": 2001,
            "name": "Sunday long run",
            "date": "2026-03-01T09:00:00Z",
            "distance_km": 20.0,
            "pace": "5:20",
            "avg_hr": 140.0,
            "elevation_m": 120.0,
            "type": "Run",
        }
    ]
    index_activities(act)
    index_activities(act)  # second upsert — must not raise

    results = query_memories("long run")
    assert len(results) == 1  # still only one entry


def test_index_activities_graceful_failure(tmp_data_dir: Path) -> None:
    """index_activities should return 0 when ChromaDB is unavailable."""
    from memory import store

    with patch.object(store, "_get_collection", return_value=None):
        count = store.index_activities(
            [{"id": 99, "name": "Run", "date": "2026-03-01"}]
        )
    assert count == 0
