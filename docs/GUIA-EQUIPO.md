# Guía para el equipo — cómo funciona el Agente Comercial (RAG + SQL)

Documento de referencia para explicar el proyecto al equipo: qué hace, cómo está
montado, qué decisión hay detrás de cada pieza y qué límites tiene esta fase.

Índice:
1. Qué problema resuelve
2. Visión general de la arquitectura
3. El ciclo de una pregunta, paso a paso
4. Módulo por módulo
5. Los guardarraíles de seguridad
6. Decisiones de diseño y por qué
7. Datos de ejemplo
8. Limitaciones conocidas de esta fase
9. Cómo extenderlo (siguientes pasos)
10. Glosario

---

## 1. Qué problema resuelve

Un comercial necesita dos tipos de información que hoy están en sitios distintos:

- **Datos**: "¿quién es mi cliente que más compra?", "¿cuántos pedidos llevamos
  este año?". Están en una base de datos relacional (SQL Server).
- **Documentos**: "¿qué descuento puedo aplicar?", "¿cuál es la política de
  devoluciones?". Están en documentos internos (PDF, Word, texto).

El agente ofrece **una sola conversación** para las dos cosas. El usuario
pregunta en lenguaje natural y el sistema decide solo dónde buscar.

Dos técnicas:

- **Text-to-SQL**: un modelo de lenguaje traduce la pregunta a una consulta SQL,
  la ejecuta (solo lectura) y explica el resultado.
- **RAG** (*Retrieval-Augmented Generation*): se buscan los fragmentos de
  documento más parecidos a la pregunta y se le pasan al modelo como contexto
  para que responda **basándose solo en ellos**.

---

## 2. Visión general de la arquitectura

```
┌───────────────────────────── app.py  (Streamlit, chat) ──────────────────────────────┐
│                                                                                       │
│   pregunta del usuario                                                                 │
│          │                                                                             │
│          ▼                                                                             │
│   src/router.py  ── LangGraph ──────────────────────────────────────────────────────   │
│          │                                                                             │
│    ┌─────┴─────────┐  nodo "classify": el LLM etiqueta la pregunta                     │
│    │               │  como  sql | rag | mixta                                          │
│    ▼               ▼                                                                   │
│  nodo sql        nodo rag                                                              │
│    │               │                                                                   │
│    ▼               ▼                                                                   │
│ src/sql_agent   src/rag_agent                                                          │
│  ReAct agent     retriever + prompt                                                    │
│  + 3 tools           │                                                                 │
│    │                 ▼                                                                  │
│    │           Chroma (data/chroma_db)  ◀── src/ingest.py indexa data/docs/*           │
│    ▼                                                                                    │
│ src/guardrails.py  ── valida "solo SELECT" + inyecta TOP N + log                       │
│    │                                                                                    │
│    ▼                                                                                    │
│ SQL Server (contenedor Docker)   ── usuario  agente_readonly  (db_datareader)          │
│                                                                                        │
│   ┌────────────────── nodo "synthesize" ──────────────────┐                            │
│   │  sql  -> devuelve la respuesta del agente SQL          │                            │
│   │  rag  -> devuelve la respuesta del agente RAG          │                            │
│   │  mixta-> el LLM combina ambas en una sola respuesta    │                            │
│   └───────────────────────────────────────────────────────┘                            │
│          │                                                                             │
│          ▼                                                                             │
│   respuesta + "Detalle": ruta usada, SQL ejecutado, documento citado                   │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

Componentes externos:

| Componente | Rol | Dónde corre |
|---|---|---|
| **LLM** (DeepSeek u OpenAI) | Clasificar, generar SQL, redactar respuestas | API externa (de pago por uso) |
| **Embeddings** | Convertir texto en vectores para la búsqueda del RAG | Local (`fastembed`, sin coste) o API OpenAI |
| **SQL Server 2022** | Almacén de datos estructurados | Contenedor Docker en el PC |
| **Chroma** | Vector store del RAG (persistido en disco) | Carpeta `data/chroma_db/` |
| **Streamlit** | Interfaz de chat | Proceso local, `localhost:8501` |

---

## 3. El ciclo de una pregunta, paso a paso

Ejemplo: *"¿Qué descuento máximo puedo aplicar al cliente que más ha comprado?"*

1. **Streamlit** (`app.py`) recibe el texto y llama a `run_router(pregunta)`.
2. **Router — nodo `classify`** (`src/router.py`): el LLM recibe la pregunta con
   un prompt que trae ejemplos (*few-shot*) y responde una palabra. Aquí:
   `mixta` (necesita datos **y** documento).
3. **Router — nodo `sql`**: llama a `answer_sql(pregunta)`.
   - El **agente ReAct** (`src/sql_agent.py`) razona en bucle: puede llamar a
     `list_tables`, `describe_tables` para ver el esquema, y finalmente escribe
     una consulta y la pasa a `run_safe_query`.
   - `run_safe_query` **no ejecuta nada todavía**: primero llama a
     `guardrails.validate_select_only`. Si el SQL no es un `SELECT` puro, se
     rechaza y el agente recibe el mensaje de bloqueo.
   - Si pasa la validación, `guardrails.enforce_row_limit` añade `TOP 100` si no
     hay límite, se registra en `logs/agente.log` y se ejecuta contra SQL Server
     con el usuario **de solo lectura**.
   - El agente recibe las filas y redacta una respuesta en español.
4. **Router — nodo `rag`** (porque era `mixta`): llama a `answer_rag(pregunta)`.
   - Se convierte la pregunta en un vector (embeddings) y se piden a **Chroma**
     los 4 fragmentos más similares (todos de `politica-descuentos-comerciales.txt`).
   - Esos fragmentos + la pregunta van al LLM con la instrucción de responder
     **solo con ese contexto** y **citar el archivo**.
5. **Router — nodo `synthesize`**: como la ruta fue `mixta`, el LLM recibe las
   dos respuestas parciales y las funde en una sola, coherente y sin repetir.
6. **Streamlit** muestra la respuesta final y, en un desplegable "Detalle", la
   ruta (`mixta`), el SQL ejecutado y el documento citado.

Para una pregunta puramente de datos, el router salta el nodo `rag`; para una
puramente documental, salta el nodo `sql`.

---

## 4. Módulo por módulo

### `src/config.py` — configuración única
- Carga `.env` (con `python-dotenv`). **Ningún otro archivo lee `os.environ`.**
- Valida que las variables obligatorias existan y da mensajes claros si faltan.
- Configura el **logging**: consola + archivo rotatorio `logs/agente.log`.
- Fábricas cacheadas:
  - `get_llm()` → cliente de chat (`ChatOpenAI` apuntando a DeepSeek u OpenAI).
  - `get_embeddings()` → `fastembed` local o `OpenAIEmbeddings`.
  - `get_sql_engine()` → engine SQLAlchemy con **timeout de consulta** a nivel de
    driver.
  - `get_sql_database()` → wrapper `SQLDatabase` de LangChain **restringido** al
    esquema `SalesLT` y a una lista blanca de tablas.

### `src/guardrails.py` — el candado de seguridad
- `validate_select_only(sql)`: limpia el texto (quita ```` ```sql ````,
  comentarios, `;` final), y **rechaza** si:
  - hay más de una sentencia;
  - no empieza por `SELECT` o `WITH`;
  - aparece cualquier palabra de escritura/DDL/exec
    (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `EXEC`,
    `MERGE`, `GRANT`, `sp_`, `xp_`, ...);
  - hay `SELECT ... INTO` (crea tablas).
- `enforce_row_limit(sql, n)`: si no hay `TOP` ni `OFFSET/FETCH`, inyecta
  `TOP n` tras el `SELECT`.
- `log_query(sql, source)`: deja constancia de **toda** consulta que se va a
  ejecutar.

### `src/sql_agent.py` — Text-to-SQL
- Agente **ReAct** (`langgraph.prebuilt.create_react_agent`) con `get_llm()` y
  tres herramientas propias:
  - `list_tables()` — lista blanca de tablas.
  - `describe_tables(tablas)` — DDL + 2 filas de muestra de cada tabla.
  - `run_safe_query(sql)` — **único** punto que toca la BD; pasa por guardrails.
- `SYSTEM_PROMPT` explica el negocio (qué es un cliente, qué tabla tiene los
  importes, etc.) para reducir errores de esquema, y obliga a responder en
  lenguaje natural, no en tabla cruda.
- `answer_sql()` nunca lanza excepción: si algo falla, lo devuelve en `error`.
  También extrae la lista de SQL realmente ejecutados para mostrarla en la UI.

### `src/ingest.py` — indexado del RAG (se ejecuta a mano)
- Lee `data/docs/` (`.pdf`, `.docx`, `.txt`, `.md`), guarda el nombre de archivo
  en el metadato `source`.
- Trocea con `RecursiveCharacterTextSplitter` (~3200 caracteres, solape ~400).
- Calcula embeddings y guarda los vectores en **Chroma** (`data/chroma_db/`).
- `python -m src.ingest` reconstruye el índice; `--append` añade sin borrar.
- **No** se ejecuta al arrancar la app: solo cuando cambian los documentos.

### `src/rag_agent.py` — RAG
- Carga el retriever de Chroma (top-k = 4).
- Prompt estricto: *responde solo con el contexto; si no está, di "No tengo esa
  información en los documentos disponibles"; cita el archivo*.
- `answer_rag()` devuelve `answer` + `sources` (archivos usados) + `error`.

### `src/router.py` — orquestador (LangGraph)
- Estado tipado (`RouterState`) que va pasando por los nodos.
- Nodos: `classify` → (`sql` y/o `rag`) → `synthesize`.
- Aristas condicionales:
  - tras `classify`: a `sql` si es `sql`/`mixta`, a `rag` si es `rag`.
  - tras `sql`: a `rag` si era `mixta`, si no a `synthesize`.
- `classify` normaliza la salida del LLM y, ante la duda, cae en `sql`.
- `run_router()` es el **único** punto de entrada para la app y nunca lanza.

### `app.py` — interfaz
- Chat de Streamlit con historial en memoria de sesión.
- Spinner "Pensando..." mientras corre `run_router`.
- Cada respuesta trae un desplegable con la ruta, el SQL y el documento fuente.
- Barra lateral con la configuración activa (sin secretos) y botón de limpiar.

---

## 5. Los guardarraíles de seguridad

Por qué son el corazón de la PoC: un modelo de lenguaje **puede** generar SQL
destructivo (por error o inducido). La defensa es **en capas**:

| Capa | Qué impide | Dónde |
|---|---|---|
| Usuario `agente_readonly` con rol `db_datareader` y `DENY` de escritura | Que *cualquier* cosa que no sea lectura llegue a ejecutarse | `sql/01_crear_usuario_readonly.sql` |
| `validate_select_only` | Que el texto llegue siquiera a la BD si no es un `SELECT` único | `src/guardrails.py` |
| Lista blanca de tablas / esquema | Que el agente vea o consulte tablas fuera de alcance | `src/config.py` (`SQL_INCLUDE_TABLES`) |
| `enforce_row_limit` | Respuestas gigantes que saturen memoria o la API | `src/guardrails.py` |
| Timeout de consulta | Consultas que se cuelguen | `src/config.py` (`get_sql_engine`) |
| Log de cada consulta | Auditoría de qué se ejecutó y cuándo | `logs/agente.log` |

Aunque falle una capa, las otras siguen. El usuario de BD de solo lectura es la
garantía última: aunque se colara un `DELETE`, SQL Server lo rechazaría.

> Nota: `validate_select_only` es deliberadamente conservador. Puede dar un falso
> positivo si un texto legítimo contiene una palabra reservada (p. ej. la función
> `REPLACE(...)`). En esa situación se prefiere bloquear y ajustar, no relajar la
> regla.

---

## 6. Decisiones de diseño y por qué

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| LLM por API (DeepSeek/OpenAI) | Modelo local | PoC en un PC; sin GPU potente. La API es barata y suficiente. |
| Embeddings **locales** por defecto | Embeddings por API | Coste cero y sin enviar los documentos a un tercero. |
| Herramientas SQL **propias** en vez del `SQLDatabaseToolkit` completo | Toolkit estándar de LangChain | Poder meter los guardrails **dentro** de la única herramienta que ejecuta SQL. |
| Router con **LangGraph** | `if/else` a mano | El grafo deja explícito el flujo, es fácil añadir nodos (caché, reintentos, más fuentes). |
| Chroma persistido en disco | Vector store en memoria | El índice sobrevive a reinicios; `ingest` se corre solo cuando cambian los docs. |
| `AdventureWorksLT` | Base inventada | Datos realistas de ventas ya modelados; ahorra tiempo. |
| Streamlit | API + front propio | Montar un chat funcional en minutos para validar la idea. |
| Todo secreto en `.env` | Config en código | Nunca subir claves a git; `.env` está en `.gitignore`. |

---

## 7. Datos de ejemplo

- **Base de datos**: `AdventureWorksLT`, esquema `SalesLT` (fabricante de
  bicicletas). Tablas usadas: `Customer`, `Address`, `CustomerAddress`,
  `Product`, `ProductCategory`, `ProductModel`, `SalesOrderHeader`,
  `SalesOrderDetail`.
- **Documentos** (`data/docs/`, ficticios, redactados para la PoC):
  - `ficha-producto-bicicleta-montana.txt` — specs, precio, garantía, FAQ.
  - `politica-descuentos-comerciales.txt` — tramos por volumen, pronto pago,
    límites de autorización.
  - `politica-devoluciones-y-garantia.txt` — plazos, RMA, exclusiones.

Los datos de la BD y los documentos **no están relacionados entre sí** (los docs
no mencionan clientes reales de la base); una pregunta "mixta" combina un dato
calculado con una regla de política.

---

## 8. Limitaciones conocidas de esta fase

- **Un solo usuario, local.** Sin autenticación, sin concurrencia, sin despliegue.
- **Sin memoria entre sesiones.** El historial vive mientras la pestaña esté
  abierta.
- **El router puede equivocar la ruta** en preguntas ambiguas; ante la duda va a
  `sql`. Se corrige afinando los ejemplos del prompt.
- **Coste y latencia.** El agente SQL hace varias llamadas al LLM por pregunta
  (razonamiento ReAct). Con `deepseek-chat` / `gpt-4o-mini` es asumible.
- **El timeout de consulta** depende del driver ODBC; no es un límite duro de
  SQL Server.
- **Text-to-SQL no es infalible**: puede generar una consulta que corre pero
  responde a una interpretación distinta de la pregunta. Por eso la UI muestra
  siempre el SQL ejecutado.
- **RAG limitado a coincidencia semántica simple** (top-k, sin *re-ranking* ni
  troceado por secciones).

---

## 9. Cómo extenderlo (siguientes pasos)

- **Memoria de conversación** en los agentes (pasar el historial al LLM).
- **Re-ranking** en el RAG y troceado por secciones/encabezados.
- **Caché** de preguntas frecuentes (nodo extra en el grafo).
- **Métricas**: registrar acierto de ruta, latencia y coste por pregunta.
- **Más fuentes**: un CRM, una hoja de cálculo, una API — se añaden como nodos.
- **Autenticación y despliegue** (contenedor de la app, no solo de la BD) cuando
  pase de PoC a piloto.
- **Evaluación automática**: convertir `tests/preguntas_prueba.md` en un test
  con respuestas esperadas.

---

## 10. Glosario

- **LLM**: modelo de lenguaje grande (el que "razona" y redacta).
- **RAG**: recuperar fragmentos relevantes y dárselos al LLM como contexto para
  que no invente.
- **Embedding**: representación numérica (vector) de un texto; textos parecidos
  tienen vectores cercanos.
- **Vector store**: base de datos de vectores para buscar por similitud (Chroma).
- **Text-to-SQL**: traducir una pregunta en lenguaje natural a una consulta SQL.
- **Agente ReAct**: patrón en el que el LLM alterna *razonar* y *usar
  herramientas* hasta tener la respuesta.
- **Router / orquestador**: la lógica que decide qué agente atiende cada
  pregunta.
- **Guardrail**: validación que impide que el sistema haga algo no permitido.
- **LangGraph**: librería para definir el flujo del agente como un grafo de
  nodos y aristas.
