"""
sql_agent.py
============
Agente de Text-to-SQL sobre la base AdventureWorksLT (esquema SalesLT).

Diseño:
  * Se usa un agente ReAct (LangGraph) con TRES herramientas propias:
        - list_tables        : lista las tablas visibles
        - describe_tables    : DDL + filas de muestra de tablas concretas
        - run_safe_query     : ejecuta UNA consulta, pasando SIEMPRE por guardrails
  * `run_safe_query` es el único punto que toca la base de datos y valida
    con `guardrails.validate_select_only` + `enforce_row_limit` antes de ejecutar.
  * El system prompt describe el negocio para reducir errores de esquema.

Función pública:
    answer_sql(pregunta: str) -> dict con:
        answer   : respuesta en lenguaje natural
        queries  : lista de SQL realmente ejecutados
        error    : str | None
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from . import guardrails
from .config import (
    SQL_INCLUDE_TABLES,
    SQL_MAX_ROWS,
    SQL_SCHEMA,
    get_llm,
    get_logger,
    get_sql_database,
)

logger = get_logger("sql_agent")

SYSTEM_PROMPT = f"""\
Eres un analista de datos que ayuda al equipo comercial a consultar la base de \
datos AdventureWorksLT (un fabricante de bicicletas y accesorios). Trabajas sobre \
el esquema `{SQL_SCHEMA}`.

Tablas disponibles y su significado de negocio:
- Customer: clientes (personas de contacto y su empresa, CompanyName). Campos útiles:
  CustomerID, FirstName, LastName, CompanyName, EmailAddress.
- SalesOrderHeader: pedidos de venta. CustomerID, OrderDate, SubTotal, TotalDue, Status.
  "compras de un cliente" = pedidos de ese CustomerID. El importe del pedido es TotalDue.
- SalesOrderDetail: líneas de cada pedido. SalesOrderID, ProductID, OrderQty, UnitPrice, LineTotal.
- Product: productos. ProductID, Name, ProductNumber, Color, ListPrice, ProductCategoryID.
- ProductCategory: categorías de producto (Name, ParentProductCategoryID).
- Address / CustomerAddress: direcciones de los clientes (City, StateProvince, CountryRegion).

Reglas:
1. SOLO puedes hacer consultas SELECT de lectura. Nunca INSERT/UPDATE/DELETE/DDL.
2. Antes de escribir SQL, consulta el esquema con `describe_tables` si tienes dudas
   sobre nombres de columnas.
3. Usa siempre el prefijo de esquema `{SQL_SCHEMA}.` en las tablas.
4. Limita los resultados (TOP N) salvo que pidan un conteo o agregado.
5. Tras obtener los datos, RESPONDE SIEMPRE en español y en lenguaje natural,
   resumiendo el resultado. No te limites a devolver la tabla en crudo.
6. Si la pregunta no se puede responder con estas tablas, dilo con claridad.
"""

_agent = None


# --------------------------------------------------------------------------
#  Herramientas
# --------------------------------------------------------------------------
@tool
def list_tables() -> str:
    """Lista las tablas del esquema comercial que se pueden consultar."""
    return ", ".join(f"{SQL_SCHEMA}.{t}" for t in SQL_INCLUDE_TABLES)


@tool
def describe_tables(tables: str) -> str:
    """
    Devuelve el DDL (CREATE TABLE) y filas de muestra de una o varias tablas.
    `tables`: nombres separados por coma, con o sin el prefijo de esquema.
    Ej: "Customer, SalesOrderHeader"
    """
    db = get_sql_database()
    wanted = []
    for raw in tables.split(","):
        name = raw.strip().split(".")[-1]
        if name in SQL_INCLUDE_TABLES:
            wanted.append(name)
        else:
            return (
                f"Tabla '{name}' no disponible. "
                f"Tablas válidas: {', '.join(SQL_INCLUDE_TABLES)}"
            )
    try:
        return db.get_table_info(table_names=wanted or None)
    except Exception as exc:  # pragma: no cover - depende de la BD
        logger.exception("Error obteniendo esquema")
        return f"No se pudo obtener el esquema: {exc}"


@tool
def run_safe_query(query: str) -> str:
    """
    Ejecuta UNA consulta SELECT de solo lectura contra la base comercial y
    devuelve las filas resultantes. Rechaza cualquier sentencia que no sea de lectura.
    """
    try:
        clean = guardrails.validate_select_only(query)
    except guardrails.GuardrailViolation as exc:
        logger.warning("Consulta bloqueada por guardrails: %s", exc)
        return f"CONSULTA BLOQUEADA POR SEGURIDAD: {exc}"

    clean = guardrails.enforce_row_limit(clean, SQL_MAX_ROWS)
    guardrails.log_query(clean, source="sql_agent")

    try:
        result = get_sql_database().run(clean)
    except Exception as exc:  # pragma: no cover - depende de la BD
        logger.exception("Error ejecutando SQL")
        return f"Error al ejecutar la consulta: {exc}"

    return result or "La consulta no devolvió filas."


TOOLS = [list_tables, describe_tables, run_safe_query]


# --------------------------------------------------------------------------
#  Agente
# --------------------------------------------------------------------------
def _get_agent():
    global _agent
    if _agent is None:
        logger.info("Inicializando agente SQL (modelo=%s)", get_llm().model_name)
        _agent = create_react_agent(get_llm(), TOOLS, prompt=SYSTEM_PROMPT)
    return _agent


def _extract_executed_queries(messages: list[Any]) -> list[str]:
    queries: list[str] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            if call.get("name") == "run_safe_query":
                q = (call.get("args") or {}).get("query")
                if q:
                    queries.append(" ".join(q.split()))
    return queries


def answer_sql(pregunta: str) -> dict:
    """Responde una pregunta de datos. No lanza excepciones: las captura en 'error'."""
    logger.info("Pregunta SQL: %s", pregunta)
    try:
        result = _get_agent().invoke(
            {"messages": [{"role": "user", "content": pregunta}]},
            config={"recursion_limit": 25},
        )
        messages = result["messages"]
        answer = messages[-1].content if messages else ""
        return {
            "answer": answer or "No obtuve respuesta.",
            "queries": _extract_executed_queries(messages),
            "error": None,
        }
    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo en answer_sql")
        return {
            "answer": (
                "Hubo un problema al consultar la base de datos. "
                "Revisa logs/agente.log para el detalle."
            ),
            "queries": [],
            "error": str(exc),
        }


if __name__ == "__main__":  # prueba rápida manual
    import sys

    q = " ".join(sys.argv[1:]) or "¿Cuántos clientes hay en la base de datos?"
    out = answer_sql(q)
    print("\n--- RESPUESTA ---\n", out["answer"])
    print("\n--- SQL EJECUTADO ---")
    for s in out["queries"]:
        print(" ", s)
