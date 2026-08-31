"""
rag_agent.py
============
Agente de recuperación (RAG) sobre los documentos indexados en Chroma.

Flujo:
    pregunta -> retriever (top-k fragmentos) -> LLM con contexto -> respuesta citando fuente

Función pública:
    answer_rag(pregunta: str) -> dict con:
        answer   : respuesta en lenguaje natural (o admisión de que no hay info)
        sources  : lista de nombres de documento usados como contexto
        error    : str | None
"""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from .config import (
    CHROMA_COLLECTION,
    CHROMA_PERSIST_DIR,
    get_embeddings,
    get_llm,
    get_logger,
)

logger = get_logger("rag_agent")

TOP_K = 4

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Eres un asistente para el equipo comercial. Respondes ÚNICAMENTE con la "
            "información del CONTEXTO que se te proporciona (extractos de documentos "
            "internos).\n"
            "Reglas:\n"
            "1. Si el contexto no contiene la respuesta, di exactamente: "
            "\"No tengo esa información en los documentos disponibles.\" No inventes.\n"
            "2. Responde en español, de forma clara y concisa.\n"
            "3. Al final, cita entre paréntesis el/los documento(s) de donde sacaste "
            "la información, usando el nombre de archivo.\n",
        ),
        (
            "human",
            "CONTEXTO:\n{context}\n\n---\nPREGUNTA: {question}",
        ),
    ]
)

_retriever = None
_chain = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        from langchain_chroma import Chroma

        store = Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_PERSIST_DIR,
        )
        try:
            count = store._collection.count()
        except Exception:
            count = -1
        if count == 0:
            logger.warning(
                "El índice Chroma está vacío. Ejecuta 'python -m src.ingest' primero."
            )
        _retriever = store.as_retriever(search_kwargs={"k": TOP_K})
    return _retriever


def _format_context(docs) -> str:
    blocks = []
    for i, doc in enumerate(docs, 1):
        src = doc.metadata.get("source", "desconocido")
        blocks.append(f"[Fragmento {i} — fuente: {src}]\n{doc.page_content}")
    return "\n\n".join(blocks)


def _get_chain():
    global _chain
    if _chain is None:
        _chain = _PROMPT | get_llm() | StrOutputParser()
    return _chain


def answer_rag(pregunta: str) -> dict:
    """Responde una pregunta documental. No lanza excepciones."""
    logger.info("Pregunta RAG: %s", pregunta)
    try:
        docs = _get_retriever().invoke(pregunta)
        if not docs:
            return {
                "answer": "No tengo esa información en los documentos disponibles.",
                "sources": [],
                "error": None,
            }
        context = _format_context(docs)
        answer = _get_chain().invoke({"context": context, "question": pregunta})
        sources = sorted({d.metadata.get("source", "desconocido") for d in docs})
        return {"answer": answer, "sources": sources, "error": None}
    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo en answer_rag")
        return {
            "answer": (
                "Hubo un problema al consultar los documentos. "
                "Revisa logs/agente.log para el detalle."
            ),
            "sources": [],
            "error": str(exc),
        }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "¿Cuál es la política de devoluciones?"
    out = answer_rag(q)
    print("\n--- RESPUESTA ---\n", out["answer"])
    print("\n--- FUENTES ---", out["sources"])
