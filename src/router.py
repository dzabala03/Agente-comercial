"""
router.py
=========
Orquestador con LangGraph. Decide, para cada pregunta del usuario, si debe
resolverla el agente SQL, el agente RAG, o ambos (pregunta mixta), y compone
la respuesta final.

Grafo:

        (entrada)
            |
        [classify]  --sql-->  [sql] --------------------+
            |  \                                        |
            |   \--rag-->  [rag] ----------------------+ |
            |                                          v v
            \--mixta--> [sql] --> [rag] ----------> [synthesize] --> (fin)

Función pública:
    run_router(pregunta: str) -> dict con:
        route      : "sql" | "rag" | "mixta"
        answer     : respuesta final en lenguaje natural
        sources    : documentos citados (RAG)
        queries    : SQL ejecutado (SQL)
        error      : str | None
"""
from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from .config import get_llm, get_logger
from .rag_agent import answer_rag
from .sql_agent import answer_sql

logger = get_logger("router")

_VALID_ROUTES = {"sql", "rag", "mixta"}

_CLASSIFIER_PROMPT = """\
Clasifica la pregunta del usuario en UNA de estas categorías:

- "sql"   : necesita datos de la base de datos comercial (clientes, pedidos,
            ventas, productos, importes, cantidades, rankings, conteos...).
- "rag"   : se responde con documentos internos (políticas de descuento, de
            devoluciones, garantías, fichas de producto, procedimientos...).
- "mixta" : necesita AMBOS: datos de la base Y contenido de un documento.

Ejemplos:
P: ¿Cuántos clientes tenemos en España?                      -> sql
P: ¿Cuáles son los 5 productos más vendidos?                 -> sql
P: ¿Cuál es la política de devoluciones?                     -> rag
P: ¿Qué garantía tiene la bicicleta de montaña?             -> rag
P: ¿Qué descuento máximo puedo aplicar al cliente que más   -> mixta
   ha comprado este año?
P: Dame el pipeline de ventas y recuérdame la política de    -> mixta
   descuentos por volumen.

Responde SOLO con una palabra: sql, rag o mixta. Sin explicaciones.

PREGUNTA: {question}
CATEGORÍA:"""


class RouterState(TypedDict, total=False):
    question: str
    route: str
    sql_result: dict
    rag_result: dict
    answer: str
    sources: list
    queries: list
    error: Optional[str]


# --------------------------------------------------------------------------
#  Nodos
# --------------------------------------------------------------------------
def _classify(state: RouterState) -> RouterState:
    question = state["question"]
    try:
        raw = get_llm().invoke(_CLASSIFIER_PROMPT.format(question=question)).content
        label = (raw or "").strip().lower()
        # Normalización defensiva
        for r in _VALID_ROUTES:
            if r in label:
                label = r
                break
        else:
            label = "sql"  # por defecto, lo más seguro es intentar datos
    except Exception:
        logger.exception("Fallo del clasificador; se usa 'sql' por defecto")
        label = "sql"

    logger.info("Router: pregunta clasificada como '%s'", label)
    return {"route": label}


def _run_sql(state: RouterState) -> RouterState:
    return {"sql_result": answer_sql(state["question"])}


def _run_rag(state: RouterState) -> RouterState:
    return {"rag_result": answer_rag(state["question"])}


def _synthesize(state: RouterState) -> RouterState:
    route = state["route"]
    sql_res = state.get("sql_result") or {}
    rag_res = state.get("rag_result") or {}

    queries = sql_res.get("queries", [])
    sources = rag_res.get("sources", [])
    errors = [e for e in (sql_res.get("error"), rag_res.get("error")) if e]

    if route == "sql":
        answer = sql_res.get("answer", "")
    elif route == "rag":
        answer = rag_res.get("answer", "")
    else:  # mixta -> combinar con el LLM
        prompt = (
            "Combina las dos respuestas parciales siguientes en UNA sola respuesta "
            "coherente en español para un comercial. No repitas información.\n\n"
            f"[Respuesta con datos de la base]\n{sql_res.get('answer','(sin datos)')}\n\n"
            f"[Respuesta con documentos internos]\n{rag_res.get('answer','(sin datos)')}\n\n"
            "RESPUESTA COMBINADA:"
        )
        try:
            answer = get_llm().invoke(prompt).content
        except Exception:
            logger.exception("Fallo en la síntesis mixta; se concatenan las partes")
            answer = (
                f"{sql_res.get('answer','')}\n\n{rag_res.get('answer','')}"
            ).strip()

    return {
        "answer": answer,
        "sources": sources,
        "queries": queries,
        "error": "; ".join(errors) if errors else None,
    }


def _after_classify(state: RouterState) -> str:
    return "sql" if state["route"] in ("sql", "mixta") else "rag"


def _after_sql(state: RouterState) -> str:
    return "rag" if state["route"] == "mixta" else "synthesize"


# --------------------------------------------------------------------------
#  Construcción del grafo (una sola vez)
# --------------------------------------------------------------------------
def _build_graph():
    g = StateGraph(RouterState)
    g.add_node("classify", _classify)
    g.add_node("sql", _run_sql)
    g.add_node("rag", _run_rag)
    g.add_node("synthesize", _synthesize)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _after_classify, {"sql": "sql", "rag": "rag"})
    g.add_conditional_edges("sql", _after_sql, {"rag": "rag", "synthesize": "synthesize"})
    g.add_edge("rag", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


_graph = None


def run_router(pregunta: str) -> dict:
    """Punto de entrada único para la app. Nunca lanza: los errores van en 'error'."""
    global _graph
    if _graph is None:
        _graph = _build_graph()

    pregunta = (pregunta or "").strip()
    if not pregunta:
        return {"route": "sql", "answer": "Escribe una pregunta.", "sources": [],
                "queries": [], "error": None}

    try:
        final = _graph.invoke({"question": pregunta})
        return {
            "route": final.get("route", "sql"),
            "answer": final.get("answer", "Sin respuesta."),
            "sources": final.get("sources", []),
            "queries": final.get("queries", []),
            "error": final.get("error"),
        }
    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo global del router")
        return {
            "route": "sql",
            "answer": "Ocurrió un error procesando la pregunta. Revisa logs/agente.log.",
            "sources": [],
            "queries": [],
            "error": str(exc),
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
