"""ChromaDB-backed long-term coaching memory store."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger("pacr")


def _get_collection() -> object | None:
    """Return the ChromaDB coaching_insights collection, or None if unavailable."""
    try:
        import chromadb  # type: ignore[import-untyped]

        import _token_utils

        chroma_dir = _token_utils.DATA_DIR / "chroma"
        chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(chroma_dir))
        return client.get_or_create_collection("coaching_insights")
    except Exception:
        logger.warning("ChromaDB unavailable — vector memory disabled", exc_info=True)
        return None


def save_memory(text: str, metadata: dict[str, str | int | float]) -> bool:
    """Persist a coaching note to the vector store.

    Args:
        text: Self-contained coaching note to embed and store.
        metadata: Flat dict of string/number metadata (e.g. category, date).

    Returns:
        True if saved successfully, False if ChromaDB is unavailable or an error
        occurred.
    """
    col = _get_collection()
    if col is None:
        return False
    try:
        category = metadata.get("category", "general")
        logger.info("ChromaDB save_memory: category=%s text=%r", category, text[:80])
        col.add(documents=[text], metadatas=[metadata], ids=[str(uuid.uuid4())])  # type: ignore[union-attr]
        logger.info("ChromaDB save_memory: saved OK")
        return True
    except Exception:
        logger.warning("save_memory failed", exc_info=True)
        return False


# Strava workout_type codes → human-readable label
_WORKOUT_TYPE_LABELS: dict[int, str] = {
    0: "default run",
    1: "race",
    2: "long run",
    3: "workout",
    10: "default ride",
    11: "race",
    12: "workout ride",
}


def index_activities(activities: list[dict]) -> int:
    """Upsert Strava activities into the vector store for semantic search.

    Uses the activity ID as the document ID so re-syncing is idempotent.

    Args:
        activities: List of normalised activity dicts (as stored in activities.json).

    Returns:
        Number of activities successfully indexed, or 0 on failure.
    """
    col = _get_collection()
    if col is None:
        return 0
    if not activities:
        return 0
    try:
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, str | int | float]] = []
        for act in activities:
            act_id = act.get("id")
            if act_id is None:
                continue
            name = act.get("name", "Run")
            date = act.get("date", "")[:10]
            dist = act.get("distance_km", 0.0)
            pace = act.get("pace", "N/A")
            hr = act.get("avg_hr")
            hr_str = f", HR {hr:.0f} bpm" if hr else ""
            elev = act.get("elevation_m")
            elev_str = f", elev {elev:.0f}m" if elev else ""
            sport = act.get("type", "Run")
            workout_type_raw = act.get("workout_type")
            workout_label = (
                _WORKOUT_TYPE_LABELS.get(int(workout_type_raw), "default")
                if workout_type_raw is not None
                else "default"
            )
            text = (
                f'"{name}" on {date}: {dist:.1f}km @ {pace}/km{hr_str}{elev_str}.'
                f" Type: {sport}. Workout type: {workout_label}."
            )
            docs.append(text)
            ids.append(str(act_id))
            metas.append(
                {
                    "category": "strava_activity",
                    "date": date,
                    "type": sport,
                    "distance_km": float(dist),
                    "workout_type": workout_label,
                }
            )
        if not docs:
            return 0
        logger.info("ChromaDB index_activities: upserting %d activities", len(docs))
        col.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[union-attr]
        logger.info("ChromaDB index_activities: done")
        return len(docs)
    except Exception:
        logger.warning("index_activities failed", exc_info=True)
        return 0


def query_memories(query: str, n_results: int = 5) -> list[dict]:
    """Retrieve the most relevant coaching memories for a query.

    Args:
        query: Natural-language query to embed and search.
        n_results: Maximum number of results to return.

    Returns:
        List of dicts with keys 'text', 'metadata', 'distance'.
        Empty list if ChromaDB is unavailable or the store is empty.
    """
    col = _get_collection()
    if col is None:
        return []
    try:
        count = col.count()  # type: ignore[union-attr]
        if count == 0:
            logger.debug("ChromaDB query_memories: collection empty, skipping")
            return []
        logger.info(
            "ChromaDB query_memories: query=%r n_results=%d store_size=%d",
            query[:80],
            min(n_results, count),
            count,
        )
        results = col.query(  # type: ignore[union-attr]
            query_texts=[query],
            n_results=min(n_results, count),
            include=["documents", "metadatas", "distances"],
        )
        memories = [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                strict=True,
            )
        ]
        for m in memories:
            logger.info(
                "ChromaDB query_memories: hit dist=%.3f category=%s text=%r",
                m["distance"],
                m["metadata"].get("category", "?"),
                m["text"][:80],
            )
        return memories
    except Exception:
        logger.warning("query_memories failed", exc_info=True)
        return []
