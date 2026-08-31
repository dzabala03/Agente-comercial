"""
app.py
======
Interfaz de chat (Streamlit) para el Agente RAG + SQL.

Ejecutar desde la raíz del proyecto:
    streamlit run app.py                         # solo este PC
    ./scripts/run_lan.ps1                        # accesible desde el móvil (misma Wi-Fi)

Abre en:  http://localhost:8501
"""
from __future__ import annotations

import streamlit as st

from src.config import describe_runtime, get_logger
from src.router import prepare

logger = get_logger("app")

st.set_page_config(page_title="Agente Comercial (RAG + SQL)", page_icon="💬")


def render_detalle(meta: dict) -> None:
    """Desplegable con la trazabilidad de la respuesta."""
    with st.expander("Detalle (fuente de la respuesta)"):
        st.markdown(f"**Ruta:** `{meta.get('route', '?')}`")
        if meta.get("queries"):
            st.markdown("**SQL ejecutado:**")
            for q in meta["queries"]:
                st.code(q, language="sql")
        if meta.get("sources"):
            st.markdown("**Documentos citados:** " + ", ".join(meta["sources"]))
        if meta.get("error"):
            st.warning(f"Incidencia: {meta['error']}")


# --------------------------------------------------------------------------
#  Barra lateral
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuración")
    try:
        for k, v in describe_runtime().items():
            st.text(f"{k}: {v}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Configuración incompleta: {exc}")
    st.divider()
    st.caption(
        "PoC de un solo usuario. El agente SQL es de **solo lectura** "
        "(guardarraíles activos)."
    )
    if st.button("🗑️ Limpiar conversación"):
        st.session_state.messages = []
        st.rerun()

# --------------------------------------------------------------------------
#  Historial
# --------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("💬 Agente Comercial")
st.caption("Pregunta sobre datos de ventas/clientes o sobre las políticas y fichas internas.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("meta"):
            render_detalle(msg["meta"])

# --------------------------------------------------------------------------
#  Entrada del usuario
# --------------------------------------------------------------------------
if prompt := st.chat_input("Escribe tu pregunta..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Consultando..."):
            meta, tokens = prepare(prompt)          # parte lenta: clasificar + SQL/recuperar
        answer = st.write_stream(tokens)            # respuesta en streaming
        render_detalle(meta)

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "meta": meta}
    )
