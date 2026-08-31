"""
guardrails.py
=============
Validaciones de seguridad para el agente SQL.

Regla innegociable de esta fase:
    El agente SÓLO puede ejecutar sentencias de lectura (SELECT / WITH ... SELECT).
    Cualquier otra cosa se rechaza ANTES de tocar la base de datos.

Funciones públicas:
    clean_sql(raw)                -> str        limpia fences y espacios
    validate_select_only(raw)     -> str        valida y devuelve el SQL limpio, o lanza GuardrailViolation
    enforce_row_limit(sql, n)     -> str        inyecta TOP n si no hay límite explícito
    log_query(sql, source)        -> None       registra la consulta en logs/agente.log
"""
from __future__ import annotations

import re

from .config import get_logger

logger = get_logger("guardrails")


class GuardrailViolation(Exception):
    """Se lanza cuando una consulta SQL infringe las reglas de solo lectura."""


# Palabras prohibidas en cualquier posición (con límites de palabra).
_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|"
    r"MERGE|GRANT|REVOKE|DENY|EXEC|EXECUTE|CALL|"
    r"BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|DBCC|"
    r"OPENROWSET|OPENQUERY|OPENDATASOURCE"
    r")\b",
    re.IGNORECASE,
)

# `SELECT ... INTO nueva_tabla` crea una tabla -> prohibido.
_SELECT_INTO = re.compile(r"\bINTO\b", re.IGNORECASE)

# Procedimientos de sistema.
_SYS_PROC = re.compile(r"\b(sp_|xp_)\w+", re.IGNORECASE)

# La sentencia debe EMPEZAR por SELECT o por WITH (CTE).
_STARTS_OK = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# Detecta si ya hay un limitador de filas.
_HAS_TOP = re.compile(r"^\s*SELECT\s+(DISTINCT\s+)?TOP\s*\(?\s*\d+", re.IGNORECASE)
_HAS_FETCH = re.compile(r"\bOFFSET\b.+\bFETCH\b", re.IGNORECASE | re.DOTALL)

# Consulta de agregado global (COUNT/SUM/... sin GROUP BY): devuelve 1 fila,
# no tiene sentido inyectarle TOP.
_BARE_AGG = re.compile(
    r"^\s*SELECT\s+(COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE
)
_HAS_GROUP_BY = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)

# Para inyectar TOP n justo después de SELECT [DISTINCT].
_SELECT_PREFIX = re.compile(r"^(\s*SELECT\s+)(DISTINCT\s+)?", re.IGNORECASE)


def clean_sql(raw: str) -> str:
    """Quita bloques markdown ```sql, comentarios de línea y espacios/;* sobrantes."""
    if raw is None:
        return ""
    text = raw.strip()

    # ```sql ... ```  o  ``` ... ```
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Comentarios de línea -- ...
    text = re.sub(r"--[^\n]*", "", text)
    # Comentarios de bloque /* ... */
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    # Quitar ; final(es) y espacios
    text = text.strip().rstrip(";").strip()
    return text


def _statement_count(sql: str) -> int:
    """Cuenta sentencias separadas por ';' que no sean vacías."""
    return len([s for s in sql.split(";") if s.strip()])


def validate_select_only(raw: str) -> str:
    """
    Devuelve el SQL limpio si es una única sentencia de solo lectura.
    Lanza GuardrailViolation en cualquier otro caso.
    """
    sql = clean_sql(raw)

    if not sql:
        raise GuardrailViolation("Consulta SQL vacía.")

    if _statement_count(sql) > 1:
        raise GuardrailViolation(
            "Se detectó más de una sentencia SQL. Solo se permite una consulta SELECT."
        )

    if not _STARTS_OK.match(sql):
        raise GuardrailViolation(
            "La consulta debe empezar por SELECT o WITH. Sentencia rechazada."
        )

    if _FORBIDDEN.search(sql):
        palabra = _FORBIDDEN.search(sql).group(1).upper()
        raise GuardrailViolation(
            f"Palabra clave prohibida detectada: '{palabra}'. "
            f"El agente solo puede LEER datos."
        )

    if _SYS_PROC.search(sql):
        raise GuardrailViolation("Uso de procedimientos de sistema (sp_/xp_) no permitido.")

    if _SELECT_INTO.search(sql):
        raise GuardrailViolation("'SELECT ... INTO' no permitido (crea tablas).")

    return sql


def enforce_row_limit(sql: str, max_rows: int) -> str:
    """
    Garantiza un límite de filas. Si la consulta no trae TOP ni OFFSET/FETCH,
    inyecta `TOP {max_rows}` justo después del primer SELECT.
    Las consultas que empiezan por WITH se dejan intactas (el TOP debe ir en el
    SELECT final; se registra un aviso).
    """
    if _HAS_TOP.match(sql) or _HAS_FETCH.search(sql):
        return sql

    # Agregado global (SELECT COUNT(*) ... sin GROUP BY): ya devuelve una fila.
    if _BARE_AGG.match(sql) and not _HAS_GROUP_BY.search(sql):
        return sql

    if re.match(r"^\s*WITH\b", sql, re.IGNORECASE):
        logger.warning(
            "Consulta con CTE sin TOP explícito; no se inyecta límite automáticamente."
        )
        return sql

    def _repl(m: re.Match) -> str:
        distinct = m.group(2) or ""
        return f"{m.group(1)}{distinct}TOP {max_rows} "

    new_sql, n = _SELECT_PREFIX.subn(_repl, sql, count=1)
    if n:
        logger.info("Guardrail: se inyectó 'TOP %d' en la consulta.", max_rows)
    return new_sql


def log_query(sql: str, source: str) -> None:
    """Registra en el log toda consulta que va a ejecutarse contra la BD real."""
    compact = " ".join(sql.split())
    logger.info("SQL [%s] -> %s", source, compact)
