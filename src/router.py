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

Regla: los PLAZOS, DÍAS, CONDICIONES, PORCENTAJES o IMPORTES que fija una
política o un contrato son "rag", aunque la frase mencione "pedido" o "cliente"
o empiece por "cuántos". Solo es "sql" si hay que CONTAR o SUMAR filas reales
de la base de datos.

Ejemplos:
¿Cuántos clientes hay en España?                              -> sql
¿Cuáles son los 5 productos más vendidos?                     -> sql
¿Cuál es la política de devoluciones?                         -> rag
¿Qué garantía tiene la bici de montaña?                      -> rag
¿Ingram usa mis datos para registrarme como cliente?         -> rag
¿Qué dice la página 8 del PDF sobre datos personales?        -> rag
¿Cuántos días hay para devolver un pedido por desistimiento?  -> rag
¿Qué descuento corresponde a 30.000 € de compra anual?       -> rag
¿Qué descuento máximo aplico al cliente que más compra?       -> mixta

Responde SOLO con una palabra: sql, rag o mixta.
PREGUNTA: {question}
CATEGORÍA:"""

# Atajos por palabras clave: solo hacen cortocircuito cuando la señal es FUERTE
# y de un único tipo. Un sustantivo suelto ("clientes", "productos") NO basta:
# en ese caso se deja decidir al LLM clasificador.
# "cuántos" solo cuenta como señal de dato si va seguido (cerca) de un
# sustantivo de la base; así "¿cuántos días para devolver?" no dispara sql.
_DATA_HINTS = re.compile(
    r"(\bcu[aá]nt[oa]s?\s+(?:\w+\s+){0,2}"
    r"(?:clientes?|pedidos?|productos?|art[ií]culos?|ventas?|unidades?|"
    r"pa[ií]ses?|regiones?|filas?|registros?|categor[ií]as?)\b"
    r"|\bcu[aá]les?\s+son\b|\btop\s*\d|\branking\b|\bpromedio\b|\bmedia\b"
    r"|\btotal(?:es)?\s+de\b|\blistad[oa]\s+de\b|\bn[uú]mero\s+de\b|\bpipeline\b"
    r"|\bsuma\s+de\b|\bm[aá]ximo\b|\bm[ií]nimo\b)",
    re.I,
)
_DOC_HINTS = re.compile(
    r"\b(pol[ií]tica|garant[ií]a|devoluci[oó]n|devolver|devuelv|desistimiento|"
    r"reembolso|descuento|bonificaci[oó]n|acumulable|pronto\s+pago|reemplazo|"
    r"reposici[oó]n|plazo|procedimiento|ficha|cat[aá]logo|rma|c[oó]mo\s+se|"
    r"qu[eé]\s+dice|privacidad|datos?\s+personales?|declaraci[oó]n|aviso|"
    r"cl[aá]usula|apartado|d[ií]as?\s+(?:para|h[aá]biles|naturales|laborables)|"
    r"p[aá]g(?:ina)?\.?\s*\d|pdf|documento|contrato|manual|"
    r"seg[uú]n\s+(el|la|lo)\b)",
    re.I,
)
# Sustantivos "de datos" sin verbo de agregación: no bastan para ir a 'sql',
# pero SÍ impiden el atajo a 'rag' -> se deja decidir al LLM (puede ser 'mixta').
_DATA_NOUNS = re.compile(
    r"\b(pedidos?|ventas?|clientes?|productos?|art[ií]culos?|importes?|"
    r"factura|pipeline)\b",
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
    data_noun = bool(_DATA_NOUNS.search(pregunta))
    if data and not doc:
        return "sql"
    if doc and not data and not data_noun:
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
    if doc and (data or data_noun):
        return "mixta"
    if doc:
        return "rag"
    return "sql"


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
        out_of_scope = s["blocked"] == "fuera_de_alcance"
        if s["blocked"] and not out_of_scope:
            return meta, iter([sql_agent.BLOCKED_MSG])
        # 'fuera_de_alcance' en ruta 'mixta': seguimos solo con los documentos.
        if out_of_scope and route == "sql":
            return meta, iter([sql_agent.OUT_OF_SCOPE_MSG])
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
        if out_of_scope:
            rows = "(la base de datos no aplica a esta pregunta)"
        elif s["error"]:
            rows = "(sin datos)"
        else:
            rows = s["rows"] or "(sin filas)"
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
