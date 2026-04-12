"""Document processor — parses, chunks, and indexes documents from the context/ folder."""

from pathlib import Path
from openai import OpenAI
import chromadb

from app.config import settings

CONTEXT_DIR = Path("context")
VECTORSTORE_DIR = Path("vectorstore")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _extract_text(filepath: Path) -> str:
    """Extract text from PDF, DOCX, or TXT files."""
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


def index_documents() -> int:
    """Scan context/ folder, parse, chunk, embed, and store in ChromaDB.
    Returns number of chunks indexed."""

    CONTEXT_DIR.mkdir(exist_ok=True)

    supported = {".txt", ".md", ".pdf", ".docx"}
    # Exclude config files from RAG indexing
    SKIP_FILES = {"system-prompt.txt"}
    files = [f for f in CONTEXT_DIR.iterdir()
             if f.is_file() and f.suffix.lower() in supported and f.name not in SKIP_FILES]

    if not files:
        print("[CallPilot] No documents in context/ folder.")
        return 0

    print(f"[CallPilot] Found {len(files)} document(s) in context/")

    all_chunks, all_metas, all_ids = [], [], []

    for filepath in files:
        print(f"[CallPilot]   Processing: {filepath.name}")
        text = _extract_text(filepath)
        if not text:
            continue
        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_metas.append({"source": filepath.name, "chunk_index": i})
            all_ids.append(f"{filepath.stem}_{i}")

    if not all_chunks:
        print("[CallPilot] No text extracted from documents.")
        return 0

    print(f"[CallPilot] Generating embeddings for {len(all_chunks)} chunks...")
    embeddings = _get_embeddings(all_chunks)

    client = _get_chroma()
    try:
        client.delete_collection("callpilot_docs")
    except Exception:
        pass

    collection = client.create_collection("callpilot_docs")
    collection.add(
        ids=all_ids,
        documents=all_chunks,
        embeddings=embeddings,
        metadatas=all_metas,
    )

    print(f"[CallPilot] ✓ Indexed {len(all_chunks)} chunks from {len(files)} file(s).")
    return len(all_chunks)
