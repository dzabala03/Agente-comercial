# Agente Comercial — RAG + Text-to-SQL (PoC)

Prueba de concepto de un agente conversacional para el equipo comercial que combina:

- **RAG**: preguntas sobre documentos internos (fichas de producto, políticas de
  descuento, devoluciones, garantía).
- **Text-to-SQL**: preguntas sobre datos estructurados (clientes, pedidos, ventas,
  productos) en **SQL Server**, en modo **solo lectura**.

Un **router** (LangGraph) decide para cada pregunta si va al agente SQL, al RAG o
a ambos, y compone la respuesta final. La interfaz es un chat en **Streamlit**.

> Alcance de esta fase: uso local, un solo usuario, datos ficticios
> (AdventureWorksLT). LLM por API externa (OpenRouter, DeepSeek u OpenAI; también
> Ollama local), no autoalojado.

---

## Arquitectura en 30 segundos

```
                 ┌──────────────────────────── app.py (Streamlit) ───────────────────────────┐
   Usuario  ───▶ │  chat  ──▶  src/router.py  ──▶  clasifica: sql | rag | mixta               │
                 │                     │                                                       │
                 │        ┌────────────┴───────────┐                                           │
                 │        ▼                        ▼                                           │
                 │  src/sql_agent.py         src/rag_agent.py                                  │
                 │  (ReAct + 3 tools)        (retriever + LLM)                                 │
                 │        │                        │                                          │
                 │        ▼                        ▼                                          │
                 │  guardrails.validate ──▶  SQL Server (solo lectura)   Chroma (data/chroma_db)│
                 │                                                                             │
                 │        └──────────▶  síntesis  ◀──────────┘  ──▶  respuesta + fuente/SQL    │
                 └─────────────────────────────────────────────────────────────────────────────┘
```

Explicación detallada para el equipo: **[docs/GUIA-EQUIPO.md](docs/GUIA-EQUIPO.md)**.

---

## Estructura del repositorio

```
.
├── app.py                     # interfaz Streamlit (punto de entrada)
├── docker-compose.yml         # SQL Server 2022 en contenedor
├── requirements.txt           # dependencias de ejecución
├── requirements-dev.txt       # + pytest
├── .env.example               # plantilla de variables de entorno
├── src/
│   ├── config.py              # carga de .env, logging, fábricas (LLM, DB, embeddings)
│   ├── guardrails.py          # validación "solo SELECT" + límite de filas + log
│   ├── sql_agent.py           # agente Text-to-SQL
│   ├── rag_agent.py           # agente RAG
│   ├── ingest.py              # indexado de documentos en Chroma (se corre a mano)
│   └── router.py              # orquestador LangGraph (sql / rag / mixta)
├── data/
│   ├── docs/                  # documentos ficticios para el RAG
│   ├── backups/               # aquí va AdventureWorksLT.bak
│   └── chroma_db/             # vector store (generado por ingest.py)
├── sql/
│   ├── 01_crear_usuario_readonly.sql
│   └── 02_smoke_test.sql
├── scripts/
│   ├── setup.ps1              # crea venv e instala dependencias
│   └── restore_db.ps1         # restaura AdventureWorksLT en el contenedor
├── tests/
│   ├── test_sql_agent.py      # pruebas de los guardrails (sin DB ni LLM)
│   ├── test_router.py
│   └── preguntas_prueba.md    # checklist de validación integral (20 preguntas)
└── logs/agente.log            # log de ejecución (incluye cada SQL ejecutado)
```

---

## Puesta en marcha (resumen)

El paso a paso completo, con lo que tienes que hacer tú y cómo, está en
**[docs/PUESTA-EN-MARCHA.md](docs/PUESTA-EN-MARCHA.md)**. Resumen:

1. **Prerrequisitos**: Python 3.11+, Docker Desktop, "ODBC Driver 18 for SQL
   Server", una API key de LLM (OpenRouter con modelos `:free`, DeepSeek u OpenAI).
2. **Base de datos**: `docker compose up -d` → restaurar `AdventureWorksLT.bak`
   (`scripts/restore_db.ps1`) → crear usuario de solo lectura
   (`sql/01_crear_usuario_readonly.sql`).
3. **Python**: `./scripts/setup.ps1` (crea `venv` e instala todo) → editar `.env`.
4. **Indexar documentos**: `python -m src.ingest`.
5. **Arrancar**: `streamlit run app.py` → http://localhost:8501
6. **Validar**: `pytest` y luego la checklist de `tests/preguntas_prueba.md`.

---

## Seguridad (no negociable en esta fase)

- El agente se conecta con un usuario **`db_datareader`**, nunca con `sa`.
- `src/guardrails.py` rechaza cualquier sentencia que no sea `SELECT`/`WITH`
  **antes** de tocar la base de datos, e inyecta un `TOP N` si falta.
- Toda consulta ejecutada queda registrada en `logs/agente.log`.
- Los secretos (API key, contraseña de BD) van **solo** en `.env`, que está en
  `.gitignore`.
