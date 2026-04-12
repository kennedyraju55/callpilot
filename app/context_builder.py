"""Context builder — retrieves relevant document chunks for a call."""

from pathlib import Path
from openai import OpenAI
import chromadb

from app.config import settings

VECTORSTORE_DIR = Path("vectorstore")
TOP_K = 5


def _get_chroma() -> chromadb.ClientAPI | None:
    if not VECTORSTORE_DIR.exists():
        return None
    return chromadb.PersistentClient(path=str(VECTORSTORE_DIR))


def _get_embedding(text: str) -> list[float]:
    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.embeddings.create(model="text-embedding-3-small", input=[text])
    return resp.data[0].embedding


def retrieve_context(query: str, top_k: int = TOP_K) -> str:
    """Search vector store for relevant chunks and return formatted context."""
    client = _get_chroma()
    if not client:
        return ""

    try:
        collection = client.get_collection("callpilot_docs")
    except Exception:
        return ""

    if collection.count() == 0:
        return ""

    query_embedding = _get_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
    )

    if not results["documents"] or not results["documents"][0]:
        return ""

    parts = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        source = meta.get("source", "unknown")
        parts.append(f"[From: {source}]\n{doc}")

    return "\n\n".join(parts)
