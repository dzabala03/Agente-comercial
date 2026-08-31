"""
ingest.py
=========
Indexa los documentos de `data/docs/` en el vector store Chroma persistido en
`data/chroma_db/`.

Se ejecuta MANUALMENTE cada vez que se añaden o cambian documentos:

    python -m src.ingest              # reindexa (borra y reconstruye la colección)
    python -m src.ingest --append     # añade sin borrar lo existente

Formatos soportados: .pdf, .docx, .txt, .md
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from langchain_community.document_loaders import (
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import (
    CHROMA_COLLECTION,
    CHROMA_PERSIST_DIR,
    DOCS_DIR,
    get_embeddings,
    get_logger,
)

logger = get_logger("ingest")

# ~800 tokens ≈ 3200 caracteres; solape ~15 %.
CHUNK_SIZE = 3200
CHUNK_OVERLAP = 400

_LOADERS = {
    ".pdf": PyPDFLoader,
    ".docx": Docx2txtLoader,
    ".txt": lambda p: TextLoader(p, encoding="utf-8"),
    ".md": lambda p: TextLoader(p, encoding="utf-8"),
}


def _load_documents(docs_dir: Path) -> list:
    if not docs_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de documentos: {docs_dir}")

    files = [p for p in sorted(docs_dir.iterdir()) if p.suffix.lower() in _LOADERS]
    if not files:
        raise FileNotFoundError(
            f"No hay documentos indexables en {docs_dir}. "
            f"Formatos: {', '.join(_LOADERS)}"
        )

    documents = []
    for path in files:
        loader_factory = _LOADERS[path.suffix.lower()]
        try:
            loaded = loader_factory(str(path)).load()
        except Exception:
            logger.exception("No se pudo cargar %s", path.name)
            continue
        for doc in loaded:
            # Metadato 'source' = nombre de archivo, para poder citar la fuente.
            doc.metadata["source"] = path.name
        documents.extend(loaded)
        logger.info("Cargado %s (%d fragmento/s de origen)", path.name, len(loaded))

    return documents


def ingest(append: bool = False) -> int:
    persist_dir = Path(CHROMA_PERSIST_DIR)

    if not append and persist_dir.exists():
        logger.info("Borrando índice previo en %s", persist_dir)
        shutil.rmtree(persist_dir, ignore_errors=True)
    persist_dir.mkdir(parents=True, exist_ok=True)

    documents = _load_documents(Path(DOCS_DIR))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    logger.info("Total de fragmentos tras el troceado: %d", len(chunks))

    # Import diferido: Chroma tarda en cargar.
    from langchain_chroma import Chroma

    vectorstore = Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(persist_dir),
    )
    vectorstore.add_documents(chunks)
    logger.info(
        "Indexación completada. Colección '%s' en %s",
        CHROMA_COLLECTION,
        persist_dir,
    )
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa documentos en Chroma.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Añadir a la colección existente en vez de reconstruirla.",
    )
    args = parser.parse_args()
    try:
        n = ingest(append=args.append)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    print(f"OK: {n} fragmentos indexados en {CHROMA_PERSIST_DIR}")


if __name__ == "__main__":
    main()
