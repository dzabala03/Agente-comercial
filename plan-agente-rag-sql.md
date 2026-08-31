# Plan de construcción — Agente RAG + SQL (PoC en Python)

## Contexto del proyecto

Prueba de concepto de un agente conversacional para comerciales que combina:
- **RAG**: consultas sobre documentos (fichas de producto, políticas, contratos)
- **Text-to-SQL**: consultas sobre datos estructurados (ventas, clientes, pipeline) en SQL Server

**Entorno**: PC personal, Windows, Python puro (sin Wren AI, sin Docker excepto para SQL Server).
**LLM**: consumo de API externa (DeepSeek u OpenAI), no autoalojado.
**Alcance de esta fase**: uso local, un solo usuario (el desarrollador), datos ficticios.

---

## FASE 0 — Preparación del entorno

### 0.1 Requisitos previos a verificar
- [ ] Python 3.11 o superior instalado (`python --version`)
- [ ] Docker Desktop instalado y corriendo (solo se usará para SQL Server)
- [ ] Al menos 8 GB RAM disponibles
- [ ] Git instalado (opcional, para versionar el proyecto)
- [ ] Editor de código (VS Code recomendado)
- [ ] Cuenta creada en el proveedor del LLM (DeepSeek o OpenAI) con API key generada

### 0.2 Estructura de carpetas del proyecto

```
agente-comercial/
├── .env                          # variables de entorno (API keys, conexión DB) — NO subir a git
├── .env.example                  # plantilla sin valores reales
├── .gitignore
├── requirements.txt
├── README.md
├── docker-compose.yml            # solo para SQL Server
├── data/
│   ├── docs/                     # documentos ficticios para el RAG (PDF, DOCX, TXT)
│   └── chroma_db/                # persistencia del vector store (se genera solo)
├── src/
│   ├── __init__.py
│   ├── config.py                 # carga de variables de entorno y constantes
│   ├── sql_agent.py              # lógica del agente SQL
│   ├── rag_agent.py              # lógica del agente RAG
│   ├── router.py                 # orquestador que decide SQL vs RAG vs ambos
│   ├── ingest.py                 # script para indexar documentos en Chroma
│   └── guardrails.py             # validaciones de seguridad (solo lectura, límites)
├── app.py                        # interfaz Streamlit (punto de entrada)
├── tests/
│   ├── preguntas_prueba.md       # checklist de 15-20 preguntas para validar
│   └── test_sql_agent.py
└── logs/
    └── agente.log
```

---

## FASE 1 — Base de datos SQL Server (vía Docker)

### 1.1 docker-compose.yml para SQL Server

Crear `docker-compose.yml` en la raíz del proyecto con un servicio de SQL Server 2022 Developer Edition, exponiendo el puerto 1433, con variables de entorno para aceptar el EULA y definir la contraseña del usuario `sa`. Persistir los datos en un volumen para que no se pierdan al reiniciar el contenedor.

### 1.2 Levantar el contenedor
- [ ] Ejecutar `docker compose up -d`
- [ ] Verificar que el contenedor está corriendo (`docker ps`)
- [ ] Verificar conectividad con una herramienta cliente (Azure Data Studio, DBeaver, o `sqlcmd`)

### 1.3 Restaurar base de datos de muestra (AdventureWorks)
- [ ] Descargar el archivo `.bak` de AdventureWorks (versión LT, más liviana, es suficiente)
- [ ] Copiar el `.bak` dentro del contenedor
- [ ] Ejecutar el `RESTORE DATABASE` vía `sqlcmd` o cliente SQL
- [ ] Verificar que las tablas están accesibles (ej. `SELECT TOP 10 * FROM SalesLT.Customer`)

### 1.4 Crear usuario de solo lectura
- [ ] Crear login y usuario dedicado (ej. `agente_readonly`)
- [ ] Asignar rol `db_datareader` únicamente — **nunca** usar `sa` desde el agente
- [ ] Probar la conexión con ese usuario desde un cliente SQL antes de pasar a Python

### 1.5 Driver ODBC en el PC (necesario para pyodbc)
- [ ] Instalar "ODBC Driver 18 for SQL Server" de Microsoft (Windows)
- [ ] Verificar instalación (`odbcinst -j` en Linux/Mac, o revisar en "Orígenes de datos ODBC" en Windows)

**Criterio de éxito de esta fase**: poder correr una consulta SQL simple contra la base de datos restaurada, usando el usuario de solo lectura, desde una herramienta externa a Python.

---

## FASE 2 — Entorno Python

### 2.1 Crear entorno virtual
- [ ] `python -m venv venv`
- [ ] Activar el entorno (`venv\Scripts\activate` en Windows)

### 2.2 requirements.txt — paquetes necesarios

Definir un `requirements.txt` que incluya (Claude Code debe fijar versiones compatibles entre sí al momento de instalar):

- `langchain`
- `langchain-community`
- `langchain-openai` (o el paquete equivalente del proveedor elegido, ej. cliente DeepSeek compatible con API estilo OpenAI)
- `langgraph`
- `pyodbc`
- `sqlalchemy`
- `chromadb`
- `langchain-chroma`
- `streamlit`
- `python-dotenv`
- `unstructured` (o `pypdf` + `python-docx` si se prefiere algo más liviano, para cargar los documentos del RAG)
- `tiktoken`

### 2.3 Instalar dependencias
- [ ] `pip install -r requirements.txt`
- [ ] Verificar que no hay errores de compilación (pyodbc a veces requiere Build Tools de C++ en Windows — anotar si falla)

### 2.4 Archivo .env
- [ ] Crear `.env` con:
  - `LLM_API_KEY=`
  - `LLM_PROVIDER=deepseek` (o `openai`)
  - `LLM_MODEL=` (ej. `deepseek-chat`)
  - `SQL_SERVER_HOST=localhost`
  - `SQL_SERVER_PORT=1433`
  - `SQL_SERVER_DB=AdventureWorksLT`
  - `SQL_SERVER_USER=agente_readonly`
  - `SQL_SERVER_PASSWORD=`
  - `CHROMA_PERSIST_DIR=./data/chroma_db`
- [ ] Crear `.env.example` como plantilla (sin valores reales) para referencia
- [ ] Agregar `.env` a `.gitignore`

**Criterio de éxito de esta fase**: entorno virtual activo, todas las librerías instaladas sin errores, variables de entorno cargando correctamente desde `config.py`.

---

## FASE 3 — Agente SQL (`src/sql_agent.py`)

### 3.1 Conexión a la base de datos
- Construir la cadena de conexión SQLAlchemy hacia SQL Server usando `pyodbc` como driver, apuntando al usuario de solo lectura.
- Envolver la conexión en el objeto `SQLDatabase` de LangChain, limitando explícitamente las tablas visibles (solo el esquema `SalesLT` o las tablas relevantes para el caso comercial).

### 3.2 Guardarraíles de seguridad (`src/guardrails.py`)
- Función que valide que el SQL generado por el modelo **solo contiene `SELECT`** — rechazar cualquier `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `EXEC`.
- Límite de filas de retorno (ej. forzar `TOP 100` si el modelo no lo incluye).
- Timeout de ejecución de la consulta (ej. 10 segundos máximo).
- Log de cada consulta SQL generada y ejecutada, con timestamp.

### 3.3 Construcción del agente
- Usar `SQLDatabaseToolkit` de LangChain con el LLM configurado.
- Definir el system prompt del agente: explicarle el negocio (qué es un "cliente", qué tablas usar), pedirle que **siempre** explique en lenguaje natural el resultado, no solo devuelva la tabla cruda.
- Envolver la ejecución del agente con el validador de guardrails antes de correr cualquier SQL contra la base real.

### 3.4 Pruebas puntuales de esta fase
- [ ] Pregunta simple: "¿Cuántos clientes hay en la base de datos?"
- [ ] Pregunta con filtro: "¿Cuáles son los 5 clientes con más compras?"
- [ ] Pregunta ambigua para probar el guardrail: intentar que el agente borre o modifique datos, confirmar que se bloquea

**Criterio de éxito de esta fase**: el agente SQL responde correctamente preguntas en lenguaje natural, nunca ejecuta nada distinto a `SELECT`, y registra cada consulta en el log.

---

## FASE 4 — Agente RAG (`src/rag_agent.py` + `src/ingest.py`)

### 4.1 Preparar documentos de prueba
- [ ] Colocar 3-5 documentos ficticios en `data/docs/` (ej. una ficha de producto, una política de descuentos, un catálogo simple) — pueden ser `.pdf`, `.docx` o `.txt`

### 4.2 Script de ingesta (`src/ingest.py`)
- Cargar los documentos con el loader correspondiente según extensión.
- Trocear el contenido (`chunk_size` ~500-800 tokens, con solapamiento de ~50-100).
- Generar embeddings (usar el mismo proveedor del LLM si ofrece endpoint de embeddings, o un modelo local ligero si se prefiere evitar costo).
- Guardar los vectores en Chroma, persistiendo en `data/chroma_db/`.
- Este script se corre **manualmente** cada vez que se agregan o cambian documentos (no en cada arranque de la app).

### 4.3 Agente de recuperación (`src/rag_agent.py`)
- Cargar el índice Chroma persistido.
- Configurar el retriever (top-k, ej. 4 fragmentos más relevantes).
- Construir la cadena de respuesta: recuperar contexto → pasarlo al LLM junto con la pregunta → generar respuesta citando de qué documento salió la información.

### 4.4 Pruebas puntuales de esta fase
- [ ] Pregunta directa sobre un documento cargado (ej. "¿Cuál es la política de devoluciones?")
- [ ] Pregunta sobre algo que NO está en los documentos (verificar que el agente diga que no tiene esa información, en vez de inventar)

**Criterio de éxito de esta fase**: el agente responde con información correcta de los documentos y cita la fuente; cuando no encuentra la respuesta, lo admite en vez de alucinar.

---

## FASE 5 — Orquestador (`src/router.py`)

### 5.1 Diseño del router con LangGraph
- Nodo clasificador: recibe la pregunta del usuario y decide si es de tipo "datos" (va al agente SQL), "documental" (va al agente RAG), o "mixta" (requiere ambos).
- Nodo SQL: invoca `sql_agent.py`.
- Nodo RAG: invoca `rag_agent.py`.
- Nodo de síntesis: si la pregunta fue mixta, combina ambas respuestas en una sola respuesta coherente.

### 5.2 Prompt del clasificador
- Definir ejemplos claros en el prompt de qué tipo de pregunta va a cada rama (few-shot examples), para minimizar errores de enrutamiento.

### 5.3 Pruebas puntuales de esta fase
- [ ] Pregunta claramente SQL — confirmar que enruta bien
- [ ] Pregunta claramente RAG — confirmar que enruta bien
- [ ] Pregunta mixta (ej. "¿Cuál es la política de descuento para mi cliente con más compras?") — confirmar que consulta ambas fuentes

**Criterio de éxito de esta fase**: el router clasifica correctamente al menos 9 de cada 10 preguntas de prueba.

---

## FASE 6 — Interfaz (`app.py` con Streamlit)

### 6.1 Construcción de la interfaz
- Chat simple con historial de conversación en memoria (session state de Streamlit).
- Cada respuesta debe mostrar de dónde vino la información (tabla SQL consultada, o documento fuente).
- Indicador de "pensando..." mientras se procesa la pregunta.

### 6.2 Ejecución local
- [ ] Correr con `streamlit run app.py`
- [ ] Verificar que abre en el navegador (`localhost:8501`) y responde correctamente

**Criterio de éxito de esta fase**: interfaz funcional donde se puede chatear con el agente de principio a fin.

---

## FASE 7 — Validación integral

### 7.1 Checklist de preguntas (`tests/preguntas_prueba.md`)
Redactar entre 15 y 20 preguntas típicas de un comercial, mezclando:
- Preguntas puramente de datos (pipeline, clientes, ventas)
- Preguntas puramente documentales (políticas, fichas de producto)
- Preguntas mixtas
- Preguntas "trampa" (pedir modificar datos, preguntar algo fuera de alcance)

### 7.2 Ejecutar el checklist completo
- [ ] Correr las 15-20 preguntas contra la app
- [ ] Anotar en una tabla: pregunta / respuesta obtenida / correcta o no / notas de ajuste

### 7.3 Ajustes finales
- Refinar prompts del router y de cada agente según los fallos encontrados.
- Confirmar que los guardrails de solo lectura funcionan en todos los casos probados.

---

## Notas para Claude Code

- Todo el código debe ser modular (un archivo por responsabilidad, como se define en la estructura de carpetas).
- Usar variables de entorno para **todo** dato sensible (API keys, credenciales de base de datos) — nunca hardcodear.
- Agregar manejo de errores explícito en la conexión a SQL Server y en las llamadas a la API del LLM (timeouts, reintentos básicos).
- Agregar logging (no solo `print`) desde el inicio, guardando en `logs/agente.log`.
- Priorizar que el agente SQL **nunca** ejecute nada distinto a `SELECT`, esto es innegociable incluso en esta fase de prueba.
- El objetivo de esta fase es funcionalidad local para un solo usuario — no optimizar aún para concurrencia ni despliegue remoto.
