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
            desc = act.get("description", "").strip()
            desc_str = f' Notes: "{desc[:120]}".' if desc else ""
            laps = act.get("laps") or []
            if len(laps) > 1:
                lap_paces = [lp.get("pace", "") for lp in laps if lp.get("pace")]
                lap_str = f" Laps ({len(laps)}): {', '.join(lap_paces)}."
            else:
                lap_str = ""
            text = (
                f'"{name}" on {date}: {dist:.1f}km @ {pace}/km{hr_str}{elev_str}.'
                f" Type: {sport}. Workout type: {workout_label}."
                f"{desc_str}{lap_str}"
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
        _BATCH = 5000
        for start in range(0, len(docs), _BATCH):
            end = start + _BATCH
            col.upsert(  # type: ignore[union-attr]
                documents=docs[start:end],
                metadatas=metas[start:end],
                ids=ids[start:end],
            )
        logger.info("ChromaDB index_activities: done")
        return len(docs)
    except Exception:
        logger.warning("index_activities failed", exc_info=True)
        return 0


def index_debriefs(debriefs: dict[str, dict]) -> int:
    """Upsert post-run debrief notes into the vector store.

    Uses ``"debrief_{activity_id}"`` as the document ID so re-indexing is
    idempotent and there is no collision with plain activity IDs.

    Args:
        debriefs: Dict keyed by str(activity_id) as returned by load_debriefs().

    Returns:
        Number of debriefs successfully indexed, or 0 on failure.
    """
    col = _get_collection()
    if col is None:
        return 0
    if not debriefs:
        return 0
    try:
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, str | int | float]] = []
        for act_id_str, d in debriefs.items():
            name = d.get("activity_name", "Run")
            date = d.get("activity_date", "")[:10]
            rpe = d.get("rpe", 0)
            notes = d.get("notes", "").strip()
            text = f'RPE {rpe}/10 after "{name}" on {date}'
            if notes:
                text += f": {notes}"
            docs.append(text)
            ids.append(f"debrief_{act_id_str}")
            metas.append(
                {
                    "category": "debrief",
                    "date": date,
                    "rpe": int(rpe),
                    "activity_id": act_id_str,
                }
            )
        if not docs:
            return 0
        logger.info("ChromaDB index_debriefs: upserting %d debriefs", len(docs))
        col.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[union-attr]
        logger.info("ChromaDB index_debriefs: done")
        return len(docs)
    except Exception:
        logger.warning("index_debriefs failed", exc_info=True)
        return 0


def index_wellness(entries: list[dict]) -> int:
    """Upsert wellness log entries into the vector store.

    Uses ``"wellness_{id}"`` as the document ID for idempotent re-indexing.

    Returns:
        Number of entries successfully indexed, or 0 on failure.
    """
    col = _get_collection()
    if col is None:
        return 0
    if not entries:
        return 0
    try:
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, str | int | float]] = []
        for e in entries:
            eid = e.get("id", "")
            if not eid:
                continue
            date = e.get("date", "")
            etype = e.get("type", "pain")
            part = e.get("body_part", "")
            sev = e.get("severity", 0)
            status = e.get("status", "active")
            notes = e.get("notes", "").strip()
            text = f"{etype.title()} in {part}, severity {sev}/10 on {date} ({status})"
            if notes:
                text += f": {notes}"
            docs.append(text)
            ids.append(f"wellness_{eid}")
            metas.append(
                {
                    "category": "wellness",
                    "date": date,
                    "body_part": part,
                    "severity": int(sev),
                    "status": status,
                }
            )
        if not docs:
            return 0
        logger.info("ChromaDB index_wellness: upserting %d entries", len(docs))
        col.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[union-attr]
        return len(docs)
    except Exception:
        logger.warning("index_wellness failed", exc_info=True)
        return 0


def index_race_results(results: list[dict]) -> int:
    """Upsert race results into the vector store.

    Uses ``"race_{date}_{event_hash}"`` as the document ID.

    Returns:
        Number of results successfully indexed, or 0 on failure.
    """
    col = _get_collection()
    if col is None:
        return 0
    if not results:
        return 0
    try:
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, str | int | float]] = []
        for r in results:
            date = r.get("date", "")
            event = r.get("event", "Race")
            distance = r.get("distance", "")
            time_str = r.get("time", "")
            notes = r.get("notes", "").strip()
            position = r.get("position")
            text = f"{event} on {date}: {distance} in {time_str}"
            if position:
                text += f", position {position}"
            if notes:
                text += f". {notes}"
            doc_id = f"race_{date}_{hash(event) & 0xFFFFFF:06x}"
            docs.append(text)
            ids.append(doc_id)
            meta: dict[str, str | int | float] = {
                "category": "race_result",
                "date": date,
                "event": event,
                "distance": distance,
            }
            if position:
                meta["position"] = int(position)
            metas.append(meta)
        if not docs:
            return 0
        logger.info(
            "ChromaDB index_race_results: upserting %d results",
            len(docs),
        )
        col.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[union-attr]
        return len(docs)
    except Exception:
        logger.warning("index_race_results failed", exc_info=True)
        return 0


def memory_stats() -> dict:
    """Return memory store statistics.

    Returns:
        Dict with keys: total, categories (dict of category->count),
        disk_mb, available.
    """
    import _token_utils

    chroma_dir = _token_utils.DATA_DIR / "chroma"
    disk_bytes = 0
    if chroma_dir.exists():
        disk_bytes = sum(f.stat().st_size for f in chroma_dir.rglob("*") if f.is_file())

    col = _get_collection()
    if col is None:
        return {
            "total": 0,
            "categories": {},
            "disk_mb": round(disk_bytes / 1024 / 1024, 1),
            "available": False,
        }
    try:
        count = col.count()  # type: ignore[union-attr]
        categories: dict[str, int] = {}
        if count > 0:
            result = col.get(include=["metadatas"])  # type: ignore[union-attr]
            for m in result["metadatas"]:
                cat = m.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1
        return {
            "total": count,
            "categories": categories,
            "disk_mb": round(disk_bytes / 1024 / 1024, 1),
            "available": True,
        }
    except Exception:
        logger.warning("memory_stats failed", exc_info=True)
        return {
            "total": 0,
            "categories": {},
            "disk_mb": round(disk_bytes / 1024 / 1024, 1),
            "available": False,
        }


def query_memories(
    query: str, n_results: int = 5, max_distance: float = 1.35
) -> list[dict]:
    """Retrieve the most relevant coaching memories for a query.

    Args:
        query: Natural-language query to embed and search.
        n_results: Maximum number of results to return.
        max_distance: Cosine distance threshold — results above this are
            discarded as too dissimilar (0 = identical, ~1.5 = unrelated).
            Default 1.35 filters out clearly irrelevant hits (>1.4)
            while keeping genuine semantic matches (~1.3 or below).

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
            if dist <= max_distance
        ]
        if memories:
            for m in memories:
                logger.info(
                    "ChromaDB query_memories: hit dist=%.3f category=%s text=%r",
                    m["distance"],
                    m["metadata"].get("category", "?"),
                    m["text"][:80],
                )
        else:
            logger.info(
                "ChromaDB query_memories: no hits within max_distance=%.2f",
                max_distance,
            )
        return memories
    except Exception:
        logger.warning("query_memories failed", exc_info=True)
        return []
