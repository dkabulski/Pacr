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
        col.add(documents=[text], metadatas=[metadata], ids=[str(uuid.uuid4())])  # type: ignore[union-attr]
        return True
    except Exception:
        logger.warning("save_memory failed", exc_info=True)
        return False


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
            return []
        results = col.query(  # type: ignore[union-attr]
            query_texts=[query],
            n_results=min(n_results, count),
            include=["documents", "metadatas", "distances"],
        )
        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                strict=True,
            )
        ]
    except Exception:
        logger.warning("query_memories failed", exc_info=True)
        return []
