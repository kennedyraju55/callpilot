"""Document processor — parses, chunks, and indexes documents from clients/ folders.

Each client gets their own ChromaDB collection named 'client_{client_id}'.
On startup, all clients found in clients/ are indexed automatically.
"""

from pathlib import Path
from openai import OpenAI
import chromadb

from app.config import settings

CLIENTS_DIR = Path("clients")
VECTORSTORE_DIR = Path("vectorstore")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Files that are config, not content — never indexed into RAG
SKIP_FILES = {"system-prompt.txt", "README.md"}


def _extract_text(filepath: Path) -> str:
    """Extract text from PDF, DOCX, TXT, or MD files."""
    suffix = filepath.suffix.lower()

    if suffix in (".txt", ".md"):
        return filepath.read_text(encoding="utf-8")

    if suffix == ".pdf":
        from PyPDF2 import PdfReader
        reader = PdfReader(str(filepath))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        from docx import Document
        doc = Document(str(filepath))
        return "\n".join(p.text for p in doc.paragraphs)

    print(f"[CallPilot] Unsupported file: {filepath.name}, skipping")
    return ""


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks."""
    if not text.strip():
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - CHUNK_OVERLAP
    return chunks


def _get_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via OpenAI."""
    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [item.embedding for item in resp.data]


def _get_chroma() -> chromadb.ClientAPI:
    VECTORSTORE_DIR.mkdir(exist_ok=True)
    return chromadb.PersistentClient(path=str(VECTORSTORE_DIR))


def get_client_name(client_id: str) -> str:
    """Extract client full name from their profile.txt. Falls back to client_id."""
    profile = CLIENTS_DIR / client_id / "profile.txt"
    if profile.exists():
        for line in profile.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("full name:"):
                name = line.split(":", 1)[1].strip()
                if name:
                    return name
    return client_id.replace("-", " ").title()


def list_clients() -> list[str]:
    """Return all client IDs found in clients/ folder."""
    if not CLIENTS_DIR.exists():
        return []
    return [d.name for d in CLIENTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]


def index_client(client_id: str) -> int:
    """Index all documents for one client into their ChromaDB collection.
    Returns number of chunks indexed."""
    client_dir = CLIENTS_DIR / client_id
    if not client_dir.exists():
        print(f"[CallPilot] Client folder not found: {client_dir}")
        return 0

    supported = {".txt", ".md", ".pdf", ".docx"}
    files = [
        f for f in client_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported and f.name not in SKIP_FILES
    ]

    collection_name = f"client_{client_id}"

    if not files:
        print(f"[CallPilot] No documents for client '{client_id}' — skipping index")
        chroma = _get_chroma()
        try:
            chroma.delete_collection(collection_name)
        except Exception:
            pass
        chroma.create_collection(collection_name)
        return 0

    print(f"[CallPilot] Indexing client '{client_id}': {len(files)} document(s)")

    all_chunks, all_metas, all_ids = [], [], []

    for filepath in files:
        print(f"[CallPilot]   Processing: {filepath.name}")
        text = _extract_text(filepath)
        if not text:
            continue
        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_metas.append({"source": filepath.name, "chunk_index": i, "client_id": client_id})
            all_ids.append(f"{client_id}_{filepath.stem}_{i}")

    if not all_chunks:
        print(f"[CallPilot] No text extracted for client '{client_id}'")
        return 0

    print(f"[CallPilot] Generating embeddings for {len(all_chunks)} chunks...")
    embeddings = _get_embeddings(all_chunks)

    chroma = _get_chroma()
    try:
        chroma.delete_collection(collection_name)
    except Exception:
        pass

    collection = chroma.create_collection(collection_name)
    collection.add(
        ids=all_ids,
        documents=all_chunks,
        embeddings=embeddings,
        metadatas=all_metas,
    )

    print(f"[CallPilot] ✓ Client '{client_id}': {len(all_chunks)} chunks indexed")
    return len(all_chunks)


def index_all_clients() -> dict[str, int]:
    """Index documents for every client found in clients/ folder.
    Returns {client_id: chunk_count}."""
    CLIENTS_DIR.mkdir(exist_ok=True)
    clients = list_clients()

    if not clients:
        print("[CallPilot] No clients found in clients/ folder.")
        return {}

    print(f"[CallPilot] Found {len(clients)} client(s): {', '.join(clients)}")
    results = {}
    for client_id in clients:
        results[client_id] = index_client(client_id)

    total = sum(results.values())
    print(f"[CallPilot] ✓ All clients indexed: {total} total chunks across {len(clients)} client(s)")
    return results
