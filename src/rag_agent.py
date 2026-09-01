"""
rag_agent.py
============
Agente de recuperación (RAG) sobre los documentos indexados en Chroma.

    retrieve(pregunta)              -> (context: str, sources: list[str])
    answer_messages(preg, context) -> list[BaseMessage]     (para streaming en el router)
    answer_rag(pregunta)           -> dict {answer, sources, error}   (sin streaming)
"""
from __future__ import annotations

from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from .config import (
    CHROMA_COLLECTION,
    CHROMA_COLLECTION_METADATA,
    CHROMA_PERSIST_DIR,
    get_embeddings,
    get_llm,
    get_logger,
)

logger = get_logger("rag_agent")

TOP_K = 5            # máximo de fragmentos que se pasan al LLM
FETCH_K = 25         # candidatos que se piden a Chroma antes de filtrar
# El modelo e5 acierta el ranking pero comprime las distancias (0.13-0.21), así
# que el filtro es RELATIVO al mejor fragmento, no absoluto:
#   - ABS_CEILING: si ni el mejor baja de aquí, no hay nada relevante -> sin
#     contexto (el LLM responde "no tengo esa información").
#   - CONTEXT_RATIO: fragmentos que ve el LLM (algo de margen).
#   - CITE_RATIO: fuentes que se muestran como cita (más estricto, para no
#     citar el PDF de privacidad cuando solo se ha colado de relleno).
ABS_CEILING = 0.19
CONTEXT_RATIO = 1.20
CITE_RATIO = 1.08

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Eres un asistente para el equipo comercial. Respondes ÚNICAMENTE con "
            "la información del CONTEXTO (extractos de documentos internos).\n"
            "- Si el contexto no contiene la respuesta, di exactamente: \"No tengo "
            "esa información en los documentos disponibles.\" No inventes.\n"
            "- Cita entre paréntesis, al final, el/los archivo(s) de donde sale la "
            "información.\n"
            "- Responde SOLO lo que se pregunta, de forma directa y breve. Sin "
            "secciones de análisis, observaciones ni recomendaciones salvo que se "
            "pidan. Sin emojis. En español.",
        ),
        ("human", "CONTEXTO:\n{context}\n\n---\nPREGUNTA: {question}"),
    ]
)

_store = None


def _get_store():
    global _store
    if _store is None:
        from langchain_chroma import Chroma

        _store = Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_PERSIST_DIR,
            collection_metadata=CHROMA_COLLECTION_METADATA,
        )
        try:
            if _store._collection.count() == 0:
                logger.warning(
                    "El índice Chroma está vacío. Ejecuta 'python -m src.ingest'."
                )
        except Exception:
            pass
    return _store


def _format_context(docs) -> str:
    blocks = []
    for i, doc in enumerate(docs, 1):
        src = doc.metadata.get("source", "desconocido")
        blocks.append(f"[Fragmento {i} — fuente: {src}]\n{doc.page_content}")
    return "\n\n".join(blocks)


def retrieve(pregunta: str) -> tuple[str, list[str]]:
    """
    Recupera los fragmentos relevantes. Devuelve (contexto_formateado, fuentes).

    Pasos: pide FETCH_K candidatos con su distancia coseno -> si ni el mejor
    baja de ABS_CEILING no hay nada relevante -> el contexto son los que estén
    dentro de best*CONTEXT_RATIO (hasta TOP_K) y las fuentes citadas solo los
    que estén dentro de best*CITE_RATIO.
    """
    pairs = _get_store().similarity_search_with_score(pregunta, k=FETCH_K)
    if not pairs:
        return "", []

    pairs.sort(key=lambda p: p[1])          # menor distancia = más relevante
    best = pairs[0][1]
    if best > ABS_CEILING:
        logger.info("RAG: nada relevante (dist. mejor=%.3f > %.2f)", best, ABS_CEILING)
        return "", []

    ctx_docs = [d for d, dist in pairs if dist <= best * CONTEXT_RATIO][:TOP_K]
    cite_docs = [d for d, dist in pairs if dist <= best * CITE_RATIO] or [pairs[0][0]]

    logger.info(
        "RAG: %d candidatos -> %d al contexto, %d fuente(s) (dist. mejor=%.3f)",
        len(pairs), len(ctx_docs), len(cite_docs), best,
    )
    sources = sorted({d.metadata.get("source", "desconocido") for d in cite_docs})
    return _format_context(ctx_docs), sources


def answer_messages(pregunta: str, context: str) -> list[BaseMessage]:
    return _PROMPT.format_messages(context=context, question=pregunta)


def answer_rag(pregunta: str) -> dict:
    """Versión sin streaming. No lanza excepciones."""
    logger.info("Pregunta RAG: %s", pregunta)
    try:
        context, sources = retrieve(pregunta)
        if not context:
            return {
                "answer": "No tengo esa información en los documentos disponibles.",
                "sources": [],
                "error": None,
            }
        answer = (get_llm(reasoning=False) | StrOutputParser()).invoke(
            answer_messages(pregunta, context)
        )
        return {"answer": answer, "sources": sources, "error": None}
    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo en answer_rag")
        return {
            "answer": "Hubo un problema al consultar los documentos.",
            "sources": [],
            "error": str(exc),
        }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "¿Cuál es la política de devoluciones?"
    out = answer_rag(q)
    print("\n--- RESPUESTA ---\n", out["answer"])
    print("\n--- FUENTES ---", out["sources"])
