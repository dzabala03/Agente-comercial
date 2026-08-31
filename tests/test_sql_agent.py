"""
Pruebas de los guardarraíles del agente SQL.
No requieren base de datos ni LLM: validan pura lógica de `src/guardrails.py`.

Ejecutar:   pytest -q
"""
import pytest

from src import guardrails as g


# ---------------------------------------------------------------------------
#  Consultas que DEBEN pasar
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM SalesLT.Customer",
        "select top 10 * from SalesLT.Product order by ListPrice desc",
        "  SELECT c.CustomerID FROM SalesLT.Customer c WHERE c.CompanyName LIKE '%bike%'  ",
        "WITH x AS (SELECT CustomerID FROM SalesLT.SalesOrderHeader) SELECT * FROM x",
        "```sql\nSELECT 1 AS uno\n```",
        "SELECT 1;",  # un ';' final es válido
    ],
)
def test_consultas_validas_pasan(sql):
    assert g.validate_select_only(sql)


# ---------------------------------------------------------------------------
#  Consultas que DEBEN ser bloqueadas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE SalesLT.Customer SET LastName='x' WHERE CustomerID=1",
        "DELETE FROM SalesLT.Customer",
        "DROP TABLE SalesLT.Customer",
        "INSERT INTO SalesLT.Customer (FirstName) VALUES ('x')",
        "TRUNCATE TABLE SalesLT.Product",
        "ALTER TABLE SalesLT.Customer ADD col INT",
        "EXEC sp_who",
        "SELECT * INTO NuevaTabla FROM SalesLT.Customer",
        "SELECT 1; DROP TABLE SalesLT.Customer",  # múltiples sentencias
        "CREATE TABLE t (id int)",
        "GRANT SELECT ON SalesLT.Customer TO public",
        "",  # vacía
    ],
)
def test_consultas_peligrosas_se_bloquean(sql):
    with pytest.raises(g.GuardrailViolation):
        g.validate_select_only(sql)


# ---------------------------------------------------------------------------
#  Límite de filas
# ---------------------------------------------------------------------------
def test_enforce_row_limit_inyecta_top():
    out = g.enforce_row_limit("SELECT * FROM SalesLT.Customer", 100)
    assert out.upper().startswith("SELECT TOP 100 ")


def test_enforce_row_limit_respeta_top_existente():
    original = "SELECT TOP 5 * FROM SalesLT.Customer"
    assert g.enforce_row_limit(original, 100) == original


def test_enforce_row_limit_respeta_distinct():
    out = g.enforce_row_limit("SELECT DISTINCT City FROM SalesLT.Address", 50)
    assert out.upper().startswith("SELECT DISTINCT TOP 50 ")


def test_enforce_row_limit_no_toca_agregados_con_top():
    original = "SELECT TOP (1) COUNT(*) FROM SalesLT.Customer"
    assert g.enforce_row_limit(original, 100) == original


# ---------------------------------------------------------------------------
#  Limpieza
# ---------------------------------------------------------------------------
def test_clean_sql_quita_fences_y_comentarios():
    raw = "```sql\nSELECT 1 -- comentario\n```"
    assert g.clean_sql(raw) == "SELECT 1"
