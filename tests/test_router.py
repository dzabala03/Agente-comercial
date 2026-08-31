"""
Pruebas ligeras del router que NO consumen la API del LLM.
Las pruebas de enrutamiento real (que sí llaman al modelo) están en
tests/preguntas_prueba.md como checklist manual.
"""
from src.router import run_router


def test_pregunta_vacia_no_llama_al_modelo():
    out = run_router("   ")
    assert out["route"] == "sql"
    assert "pregunta" in out["answer"].lower()
    assert out["error"] is None


def test_forma_de_la_respuesta():
    out = run_router("")
    assert set(out.keys()) == {"route", "answer", "sources", "queries", "error"}
