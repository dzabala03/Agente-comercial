"""
app.py
======
Interfaz de chat (Streamlit) para el Agente RAG + SQL.

Ejecutar desde la raíz del proyecto:

    streamlit run app.py

Abre en el navegador:  http://localhost:8501
"""
from __future__ import annotations

import streamlit as st

from src.config import describe_runtime, get_logger
from src.router import run_router

logger = get_logger("app")

st.set_page_config(page_title="Agente Comercial (RAG + SQL)", page_icon="💬")

# --------------------------------------------------------------------------
#  Barra lateral: configuración activa
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
#  Estado de la conversación
# --------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("💬 Agente Comercial")
st.caption("Pregunta sobre datos de ventas/clientes o sobre las políticas y fichas internas.")

# Historial
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        meta = msg.get("meta")
        if meta:
            with st.expander("Detalle (fuente de la respuesta)"):
                st.markdown(f"**Ruta:** `{meta['route']}`")
                if meta.get("queries"):
                    st.markdown("**SQL ejecutado:**")
                    for q in meta["queries"]:
                        st.code(q, language="sql")
                if meta.get("sources"):
                    st.markdown("**Documentos citados:** " + ", ".join(meta["sources"]))
                if meta.get("error"):
                    st.warning(f"Incidencia: {meta['error']}")

# --------------------------------------------------------------------------
#  Entrada del usuario
# --------------------------------------------------------------------------
if prompt := st.chat_input("Escribe tu pregunta..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Pensando..."):
            result = run_router(prompt)
        st.markdown(result["answer"])
        meta = {
            "route": result["route"],
            "queries": result["queries"],
            "sources": result["sources"],
            "error": result["error"],
        }
        with st.expander("Detalle (fuente de la respuesta)"):
            st.markdown(f"**Ruta:** `{meta['route']}`")
            if meta["queries"]:
                st.markdown("**SQL ejecutado:**")
                for q in meta["queries"]:
                    st.code(q, language="sql")
            if meta["sources"]:
                st.markdown("**Documentos citados:** " + ", ".join(meta["sources"]))
            if meta["error"]:
                st.warning(f"Incidencia: {meta['error']}")

    st.session_state.messages.append(
        {"role": "assistant", "content": result["answer"], "meta": meta}
    )
