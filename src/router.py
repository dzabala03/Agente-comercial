"""
router.py
=========
Orquestador (Python puro, sin LangGraph). Clasifica la pregunta en
`sql | rag | mixta` y produce la respuesta final EN STREAMING.

    prepare(pregunta)     -> (meta: dict, tokens: Iterable[str])
                             meta = {route, queries, sources, error}
                             tokens = generador que emite la respuesta final trozo a trozo
    run_router(pregunta)  -> dict {route, answer, sources, queries, error}   (sin streaming)

Llamadas al LLM por pregunta (frente a la versión ReAct anterior):
    - sql   : clasificar(0-1) + generar SQL(1) + explicar(1)      ~2-3
    - rag   : clasificar(0-1) + responder(1)                       ~1-2
    - mixta : clasificar(1)   + generar SQL(1) + sintetizar(1)     ~3
El clasificador se salta cuando las palabras clave son inequívocas.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

from langchain_core.prompts import ChatPromptTemplate

from . import rag_agent, sql_agent
from .config import get_llm, get_logger

logger = get_logger("router")

_VALID = {"sql", "rag", "mixta"}

_CLASSIFIER_PROMPT = """\
Clasifica la pregunta del usuario en UNA categoría:
- "sql"   : necesita datos de la base comercial (clientes, pedidos, ventas,
            productos, importes, cantidades, rankings, conteos...).
- "rag"   : se responde con documentos internos (políticas de descuento, de
            devoluciones, garantías, fichas de producto, procedimientos...).
- "mixta" : necesita AMBOS.

Ejemplos:
¿Cuántos clientes hay en España?                          -> sql
¿Cuáles son los 5 productos más vendidos?                 -> sql
¿Cuál es la política de devoluciones?                     -> rag
¿Qué garantía tiene la bici de montaña?                  -> rag
¿Qué descuento máximo aplico al cliente que más compra?   -> mixta

Responde SOLO con una palabra: sql, rag o mixta.
PREGUNTA: {question}
CATEGORÍA:"""

# Atajos por palabras clave: si la señal es de un solo tipo, no gastamos una
# llamada en clasificar.
_DATA_HINTS = re.compile(
    r"\b(cu[aá]nt[oa]s?|cu[aá]les?\s+son|top\s*\d|ranking|promedio|media|"
    r"total(es)?\s+de|listad[oa]\s+de|n[uú]mero\s+de|pipeline|factura|"
    r"pedidos?|ventas?|clientes?|productos?|importes?|comprad)\b",
    re.I,
)
_DOC_HINTS = re.compile(
    r"\b(pol[ií]tica|garant[ií]a|devoluci[oó]n|reembolso|descuento|bonificaci[oó]n|"
    r"procedimiento|ficha|cat[aá]logo|rma|c[oó]mo\s+se|qu[eé]\s+dice|"
    r"seg[uú]n\s+(el|la)\s+(documento|contrato|manual|pol[ií]tica))\b",
    re.I,
)

_SYNTH_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Respondes a un comercial combinando DATOS de la base y DOCUMENTOS "
            "internos. Usa solo la información proporcionada.\n"
            "- Si la pregunta pide un tramo, nivel o porcentaje que depende de una "
            "cifra, CALCÚLALO tú: toma la cifra de DATOS y aplícale las reglas de "
            "DOCUMENTOS (elige el tramo correcto según el importe).\n"
            "- Responde SOLO lo que se pregunta, de forma directa y breve. Sin "
            "secciones de análisis, observaciones ni recomendaciones salvo que se "
            "pidan. Sin emojis. En español. Cita el/los documento(s) usados entre "
            "paréntesis al final.",
        ),
        (
            "human",
            "PREGUNTA: {question}\n\n"
            "DATOS (resultado de `{sql}`):\n{rows}\n\n"
            "DOCUMENTOS:\n{context}",
        ),
    ]
)


def _classify(pregunta: str) -> str:
    data = bool(_DATA_HINTS.search(pregunta))
    doc = bool(_DOC_HINTS.search(pregunta))
    if data and not doc:
        return "sql"
    if doc and not data:
        return "rag"
    try:
        raw = (
            get_llm(reasoning=False)
            .bind(max_tokens=8)
            .invoke(_CLASSIFIER_PROMPT.format(question=pregunta))
            .content
            or ""
        )
        low = raw.strip().lower()
        for r in _VALID:
            if r in low:
                return r
    except Exception:
        logger.exception("Fallo del clasificador; se usa heurística")
    return "mixta" if (data and doc) else "sql"


def _stream(messages) -> Iterator[str]:
    """Emite la respuesta final trozo a trozo (sin cadena de pensamiento)."""
    try:
        for chunk in get_llm(reasoning=False).stream(messages):
            if chunk.content:
                yield chunk.content
    except Exception:  # pragma: no cover
        logger.exception("Fallo al generar la respuesta")
        yield "\n\n(Hubo un problema al generar la respuesta; revisa logs/agente.log.)"


def prepare(pregunta: str) -> tuple[dict, Iterable[str]]:
    pregunta = (pregunta or "").strip()
    meta = {"route": "sql", "queries": [], "sources": [], "error": None}
    if not pregunta:
        return meta, iter(["Escribe una pregunta."])

    route = _classify(pregunta)
    meta["route"] = route
    logger.info("Router: pregunta clasificada como '%s'", route)

    try:
        if route == "rag":
            context, sources = rag_agent.retrieve(pregunta)
            meta["sources"] = sources
            if not context:
                return meta, iter(
                    ["No tengo esa información en los documentos disponibles."]
                )
            return meta, _stream(rag_agent.answer_messages(pregunta, context))

        # --- sql o mixta: primero resolvemos el SQL ---
        s = sql_agent.solve_sql(pregunta)
        if s["sql"]:
            meta["queries"] = [s["sql"]]
        if s["blocked"]:
            return meta, iter([sql_agent.BLOCKED_MSG])
        if s["error"]:
            meta["error"] = s["error"]

        if route == "sql":
            if s["error"]:
                return meta, iter(
                    ["No pude ejecutar la consulta contra la base de datos."]
                )
            return meta, _stream(
                sql_agent.explain_messages(pregunta, s["sql"], s["rows"])
            )

        # --- mixta: añadimos los documentos y sintetizamos ---
        context, sources = rag_agent.retrieve(pregunta)
        meta["sources"] = sources
        rows = "(sin datos)" if s["error"] else (s["rows"] or "(sin filas)")
        msgs = _SYNTH_PROMPT.format_messages(
            question=pregunta,
            sql=s["sql"] or "-",
            rows=rows,
            context=context or "(sin documentos relevantes)",
        )
        return meta, _stream(msgs)

    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo global del router")
        meta["error"] = str(exc)
        return meta, iter(["Ocurrió un error procesando la pregunta."])


def run_router(pregunta: str) -> dict:
    """Punto de entrada sin streaming (tests, CLI). Nunca lanza."""
    meta, tokens = prepare(pregunta)
    answer = "".join(tokens)
    return {
        "route": meta["route"],
        "answer": answer or "Sin respuesta.",
        "sources": meta["sources"],
        "queries": meta["queries"],
        "error": meta["error"],
    }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "¿Cuántos clientes hay y cuál es la política de devoluciones?"
    out = run_router(q)
    print(f"\n[route={out['route']}]\n")
    print(out["answer"])
    if out["queries"]:
        print("\nSQL:", out["queries"])
    if out["sources"]:
        print("\nFuentes:", out["sources"])
