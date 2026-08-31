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


def test_enforce_row_limit_no_toca_count_global():
    original = "SELECT COUNT(*) FROM SalesLT.Customer"
    assert g.enforce_row_limit(original, 100) == original


def test_enforce_row_limit_si_toca_agregado_con_group_by():
    out = g.enforce_row_limit(
        "SELECT CustomerID, SUM(TotalDue) FROM SalesLT.SalesOrderHeader GROUP BY CustomerID",
        100,
    )
    assert out.upper().startswith("SELECT TOP 100 ")


# ---------------------------------------------------------------------------
#  Limpieza
# ---------------------------------------------------------------------------
def test_clean_sql_quita_fences_y_comentarios():
    raw = "```sql\nSELECT 1 -- comentario\n```"
    assert g.clean_sql(raw) == "SELECT 1"


# ---------------------------------------------------------------------------
#  Heurística de intención de escritura (sobre la pregunta del usuario)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pregunta",
    [
        "Borra el cliente con CustomerID = 1",
        "Elimina todos los pedidos de 2020",
        "Actualiza el apellido del cliente 10 a Prueba",
        "bórrame ese registro",
        "DELETE FROM SalesLT.Customer",
        "haz un UPDATE Customer SET LastName = 'x'",
        "inserta un nuevo producto",
        "cambia el precio del producto 5 a 100",
    ],
)
def test_detecta_intento_de_escritura(pregunta):
    assert g.looks_like_write_request(pregunta) is True


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuántos clientes hay?",
        "Dame los 5 productos más vendidos",
        "actualízame el pipeline de ventas",  # 'actualiza' pero sin asignar un valor
        "¿Qué clientes se dieron de baja el año pasado?",
        "muéstrame el ranking de clientes por compras",
        "¿cuál es la política de devoluciones?",
    ],
)
def test_no_bloquea_preguntas_de_lectura(pregunta):
    assert g.looks_like_write_request(pregunta) is False
