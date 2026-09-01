"""
sql_agent.py
============
Text-to-SQL sobre AdventureWorksLT (esquema SalesLT) en **2 llamadas al LLM**:

    1. GENERAR el SELECT   -> el esquema va fijo en el prompt (sin herramientas)
    2. EXPLICAR el resultado en lenguaje natural

Sin agente ReAct: menos llamadas encadenadas = respuesta más rápida.
Un único reintento si la consulta generada falla al ejecutarse.

API:
    solve_sql(pregunta)            -> dict {sql, rows, blocked, error}  (sin explicación)
    explain_messages(preg, sql, rows) -> list[BaseMessage]
    answer_sql(pregunta)          -> dict {answer, queries, error}      (incluye explicación)
    BLOCKED_MSG                    -> str
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from . import guardrails
from .config import SQL_MAX_ROWS, SQL_SCHEMA, get_llm, get_logger, get_sql_database

logger = get_logger("sql_agent")

BLOCKED_MSG = (
    "No puedo realizar esa operación: mi acceso a la base de datos es de solo "
    "lectura, únicamente consultas SELECT."
)

OUT_OF_SCOPE_MSG = (
    "Esa pregunta no se responde con los datos de la base comercial "
    "(clientes, pedidos, ventas, productos)."
)

# El generador devuelve este token cuando la pregunta no se puede responder con
# las tablas disponibles (en vez de fabricar un SELECT de valores constantes).
_NO_DATA_TOKEN = "SIN_DATOS"

# Reglas de estilo compartidas: preciso, directo, sin análisis no solicitado.
STYLE_RULES = (
    "Reglas de estilo (obligatorias):\n"
    "- Responde SOLO lo que se pregunta, de forma directa y breve.\n"
    "- NO añadas secciones de observaciones, análisis, conclusiones, contexto "
    "extra ni recomendaciones salvo que te las pidan de forma explícita.\n"
    "- Si procede un listado, usa una tabla simple o una lista corta.\n"
    "- Sin emojis. En español."
)

# Relaciones entre tablas (claves foráneas del esquema SalesLT). Darlas explícitas
# evita joins inventados.
_FK_RELATIONS = (
    "Relaciones (claves foráneas):\n"
    "- SalesOrderHeader.CustomerID = Customer.CustomerID\n"
    "- SalesOrderDetail.SalesOrderID = SalesOrderHeader.SalesOrderID\n"
    "- SalesOrderDetail.ProductID = Product.ProductID\n"
    "- Product.ProductCategoryID = ProductCategory.ProductCategoryID\n"
    "- Product.ProductModelID = ProductModel.ProductModelID\n"
    "- ProductCategory.ParentProductCategoryID = ProductCategory.ProductCategoryID\n"
    "- CustomerAddress.CustomerID = Customer.CustomerID\n"
    "- CustomerAddress.AddressID = Address.AddressID"
)

# Ejemplos (pregunta -> SELECT) de los patrones más frecuentes. Guían el estilo
# de join y agregación sin encarecer apenas el prompt.
_FEW_SHOT = """\
Ejemplos:

P: ¿Qué 5 clientes han comprado más?
SELECT TOP 5 c.CompanyName, SUM(soh.TotalDue) AS TotalCompras
FROM SalesLT.SalesOrderHeader soh
JOIN SalesLT.Customer c ON c.CustomerID = soh.CustomerID
GROUP BY c.CompanyName
ORDER BY TotalCompras DESC

P: ¿Cuánto se ha vendido por categoría de producto?
SELECT pc.Name AS Categoria, SUM(sod.LineTotal) AS TotalVendido
FROM SalesLT.SalesOrderDetail sod
JOIN SalesLT.Product p ON p.ProductID = sod.ProductID
JOIN SalesLT.ProductCategory pc ON pc.ProductCategoryID = p.ProductCategoryID
GROUP BY pc.Name
ORDER BY TotalVendido DESC

P: ¿Cuántos pedidos hubo en 2008?
SELECT COUNT(*) AS NumPedidos
FROM SalesLT.SalesOrderHeader
WHERE OrderDate >= '2008-01-01' AND OrderDate < '2009-01-01'

P: ¿Cuál es el ticket medio del cliente 29847?
SELECT AVG(TotalDue) AS TicketMedio
FROM SalesLT.SalesOrderHeader
WHERE CustomerID = 29847

P: ¿Cuáles son los 10 productos más vendidos por unidades?
SELECT TOP 10 p.Name, SUM(sod.OrderQty) AS Unidades
FROM SalesLT.SalesOrderDetail sod
JOIN SalesLT.Product p ON p.ProductID = sod.ProductID
GROUP BY p.Name
ORDER BY Unidades DESC

P: ¿Cuántos clientes hay por país?
SELECT a.CountryRegion, COUNT(DISTINCT ca.CustomerID) AS NumClientes
FROM SalesLT.CustomerAddress ca
JOIN SalesLT.Address a ON a.AddressID = ca.AddressID
GROUP BY a.CountryRegion
ORDER BY NumClientes DESC"""

_GEN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Genera UNA consulta T-SQL (Microsoft SQL Server) que responda la "
            "pregunta del usuario.\n"
            "Devuelve EXCLUSIVAMENTE la sentencia SELECT: sin explicación, sin "
            "comentarios, sin ``` y sin punto y coma final.\n\n"
            "Esquema disponible (usa SIEMPRE el prefijo `{schema}.` en las tablas):\n"
            "{schema_info}\n\n"
            + _FK_RELATIONS
            + "\n\n"
            + _FEW_SHOT
            + "\n\nNotas de negocio:\n"
            "- Si NINGUNA parte de la pregunta se puede responder con estas "
            "tablas (es enteramente sobre políticas, plazos, condiciones o "
            "cultura general), responde EXACTAMENTE con la palabra SIN_DATOS y "
            "nada más; NO inventes un SELECT de valores constantes. Pero si "
            "CUALQUIER parte pide datos de clientes/pedidos/ventas/productos "
            "(p. ej. 'qué clientes superan 50.000', 'el cliente que más compra'), "
            "genera el SELECT para ESA parte e ignora el resto.\n"
            "- Para identificar un producto en el resultado usa SIEMPRE la "
            "columna `Name`, nunca `ProductNumber`, salvo que se pida la "
            "referencia explícitamente.\n"
            "- Cualquier métrica de un cliente (cuánto compra, ranking, nº de "
            "pedidos, ticket medio) se calcula con AGREGADOS sobre "
            "{schema}.SalesOrderHeader: SUM(TotalDue), COUNT(*), AVG(TotalDue). "
            "NO uses columnas de líneas sueltas como UnitPriceDiscount.\n"
            "- Si la pregunta menciona 'descuento por volumen', 'tramo', 'nivel' o "
            "'política' aplicada a un cliente: devuelve SOLO el IMPORTE TOTAL de "
            "compras de ese cliente (SUM(TotalDue)); el tramo/porcentaje lo aplica "
            "otra capa, no lo calcules en SQL.\n"
            "- Usa TOP N para limitar filas, salvo que se pida un conteo o un "
            "agregado global.\n"
            "- Solo lectura: nada de INSERT/UPDATE/DELETE/DDL.",
        ),
        ("human", "{question}"),
    ]
)

_EXPLAIN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Respondes una pregunta de negocio a partir del resultado de una "
            "consulta SQL que YA se ejecutó.\n"
            + STYLE_RULES
            + "\nSi el resultado no tiene filas, dilo en una sola frase.",
        ),
        (
            "human",
            "Pregunta: {question}\n\nSQL ejecutado:\n{sql}\n\nResultado (filas):\n{rows}",
        ),
    ]
)


@lru_cache(maxsize=1)
def _schema_info() -> str:
    """DDL + 2 filas de muestra de las tablas permitidas. Se calcula una sola vez."""
    return get_sql_database().get_table_info()


def _generate_sql(pregunta: str, error_prev: str | None = None) -> str:
    msgs = _GEN_PROMPT.format_messages(
        schema=SQL_SCHEMA, schema_info=_schema_info(), question=pregunta
    )
    if error_prev:
        msgs.append(
            HumanMessage(
                content=(
                    f"La consulta anterior falló al ejecutarse:\n{error_prev}\n"
                    f"Devuelve una versión corregida (solo el SELECT)."
                )
            )
        )
    raw = get_llm().invoke(msgs).content
    return guardrails.clean_sql(raw)


def _check_and_prep(sql_raw: str) -> tuple[str | None, str | None]:
    """Valida guardrails + aplica límite de filas. Devuelve (sql_listo, motivo_bloqueo)."""
    try:
        clean = guardrails.validate_select_only(sql_raw)
    except guardrails.GuardrailViolation as exc:
        logger.warning("Consulta bloqueada por guardrails: %s", exc)
        return None, str(exc)
    clean = guardrails.enforce_row_limit(clean, SQL_MAX_ROWS)
    guardrails.log_query(clean, source="sql_agent")
    return clean, None


def _execute(sql: str) -> tuple[str | None, str | None]:
    try:
        return get_sql_database().run(sql), None
    except Exception as exc:  # pragma: no cover - depende de la BD
        logger.warning("Error ejecutando SQL: %s", exc)
        return None, str(exc)


def solve_sql(pregunta: str) -> dict:
    """
    Genera y ejecuta el SQL (con 1 reintento si falla). NO hace la llamada de
    explicación. Devuelve: {sql, rows, blocked, error}.
    """
    logger.info("Pregunta SQL: %s", pregunta)

    # Atajo: si la pregunta pide modificar datos, se corta ANTES de llamar al LLM.
    if guardrails.looks_like_write_request(pregunta):
        logger.warning("Intento de escritura detectado en la pregunta; bloqueado.")
        return {"sql": None, "rows": None, "blocked": "intento de escritura", "error": None}

    sql_raw = _generate_sql(pregunta)
    if sql_raw.strip().upper().startswith(_NO_DATA_TOKEN):
        logger.info("El generador marcó la pregunta como fuera del alcance de la BD.")
        return {"sql": None, "rows": None, "blocked": "fuera_de_alcance", "error": None}
    clean, blocked = _check_and_prep(sql_raw)
    if blocked:
        return {"sql": None, "rows": None, "blocked": blocked, "error": None}

    rows, err = _execute(clean)
    if err:
        logger.info("Reintento de generación de SQL tras error de ejecución.")
        sql_raw = _generate_sql(pregunta, error_prev=err)
        clean2, blocked = _check_and_prep(sql_raw)
        if blocked:
            return {"sql": None, "rows": None, "blocked": blocked, "error": None}
        clean = clean2 or clean
        rows, err = _execute(clean)

    return {"sql": clean, "rows": rows, "blocked": None, "error": err}


def explain_messages(pregunta: str, sql: str, rows: str | None) -> list[BaseMessage]:
    return _EXPLAIN_PROMPT.format_messages(
        question=pregunta, sql=sql, rows=rows or "(sin filas)"
    )


def answer_sql(pregunta: str) -> dict:
    """Versión sin streaming: resuelve + explica. No lanza excepciones."""
    try:
        s = solve_sql(pregunta)
    except Exception as exc:  # pragma: no cover
        logger.exception("Fallo en solve_sql")
        return {
            "answer": "Hubo un problema al consultar la base de datos.",
            "queries": [],
            "error": str(exc),
        }

    if s["blocked"]:
        msg = OUT_OF_SCOPE_MSG if s["blocked"] == "fuera_de_alcance" else BLOCKED_MSG
        return {"answer": msg, "queries": [], "error": None}
    if s["error"]:
        return {
            "answer": "No pude ejecutar la consulta contra la base de datos.",
            "queries": [s["sql"]] if s["sql"] else [],
            "error": s["error"],
        }

    answer = (get_llm(reasoning=False) | StrOutputParser()).invoke(
        explain_messages(pregunta, s["sql"], s["rows"])
    )
    return {"answer": answer, "queries": [s["sql"]], "error": None}


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "¿Cuántos clientes hay en la base de datos?"
    out = answer_sql(q)
    print("\n--- RESPUESTA ---\n", out["answer"])
    print("\n--- SQL EJECUTADO ---")
    for s in out["queries"]:
        print(" ", s)
