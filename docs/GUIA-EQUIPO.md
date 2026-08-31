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
│   src/router.py  ── prepare(pregunta) ──────────────────────────────────────────────   │
│          │                                                                             │
│    _classify(): palabras clave inequívocas -> sql | rag ; si hay duda, 1 llamada al LLM │
│          │                                                                             │
│    ┌─────┴───────────────┐                                                             │
│    ▼ (sql / mixta)       ▼ (rag)                                                       │
│ src/sql_agent.solve_sql  src/rag_agent.retrieve                                        │
│   1. LLM: generar SELECT   busca en Chroma los k fragmentos                            │
│      (esquema en el prompt)      más parecidos                                         │
│   2. guardrails.validate_select_only  ── "solo SELECT" + TOP N + log                   │
│   3. ejecutar (usuario readonly)   Chroma (data/chroma_db) ◀ src/ingest.py ◀ data/docs/*│
│    │                     │                                                             │
│    ▼                     ▼                                                             │
│ SQL Server 2025 (Docker, usuario agente_readonly / db_datareader)                      │
│                                                                                        │
│   respuesta final = 1 llamada al LLM EN STREAMING:                                     │
│     sql  -> explicar el resultado          rag -> responder con el contexto            │
│     mixta-> sintetizar datos + documentos                                             │
│          │                                                                             │
│          ▼                                                                             │
│   st.write_stream(...)  +  "Detalle": ruta, SQL ejecutado, documento citado           │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

Componentes externos:

| Componente | Rol | Dónde corre |
|---|---|---|
| **LLM** (OpenRouter / DeepSeek / OpenAI / Ollama) | Clasificar, generar SQL, redactar respuestas | API externa (u Ollama local) |
| **Embeddings** | Convertir texto en vectores para la búsqueda del RAG | Local (`fastembed`, sin coste) o API OpenAI |
| **SQL Server 2025** | Almacén de datos estructurados | Contenedor Docker en el PC |
| **Chroma** | Vector store del RAG (persistido en disco) | Carpeta `data/chroma_db/` |
| **Streamlit** | Interfaz de chat | Proceso local, `localhost:8501` |

---

## 3. El ciclo de una pregunta, paso a paso

Ejemplo: *"¿Qué descuento máximo puedo aplicar al cliente que más ha comprado?"*

1. **Streamlit** (`app.py`) llama a `router.prepare(pregunta)`, que hace la parte
   "lenta" (clasificar + consultar) mientras se muestra "Consultando...".
2. **`_classify`** (`src/router.py`): primero mira palabras clave. Aquí aparecen
   señales de datos (*cliente*, *comprado*) **y** de documento (*descuento*), así
   que no puede decidir sola → **1 llamada al LLM** con ejemplos *few-shot* →
   responde `mixta`.
3. **`sql_agent.solve_sql(pregunta)`** (2 pasos, sin agente ReAct):
   - **Llamada 1 al LLM**: genera un `SELECT`. El esquema de las 8 tablas va
     **fijo en el prompt**, así que no hace falta que el modelo lo pida.
   - `guardrails.validate_select_only` comprueba que es un `SELECT` único; si no,
     se rechaza y se devuelve el mensaje de bloqueo (sin más llamadas).
   - `guardrails.enforce_row_limit` añade `TOP 100` si falta (salvo conteos), se
     registra en `logs/agente.log` y se ejecuta con el usuario **de solo lectura**.
   - Si la consulta falla al ejecutarse, hay **un** reintento (se le pasa el error
     al LLM para que la corrija).
4. **`rag_agent.retrieve(pregunta)`** (porque es `mixta`): convierte la pregunta
   en vector y pide a **Chroma** los *k* fragmentos más parecidos
   (`politica-descuentos-comerciales.txt`). Esto **no** gasta llamada al LLM.
5. **Respuesta final = 1 llamada al LLM en streaming**: recibe la pregunta + las
   filas del SQL + los fragmentos, y redacta una respuesta combinada, **breve y
   sin análisis no solicitado**, citando el documento. `st.write_stream` la va
   pintando palabra a palabra en el móvil/navegador.
6. El desplegable "Detalle" muestra la ruta (`mixta`), el SQL ejecutado y el
   documento citado.

Recuento de llamadas al LLM por pregunta: **sql** ≈ 2-3, **rag** ≈ 1-2,
**mixta** ≈ 3. Para una pregunta puramente de datos se salta el RAG; para una
puramente documental se salta el SQL. Si el intento es de escritura
(`borra...`, `actualiza...`), se responde al instante **sin ninguna llamada**.

---

## 4. Módulo por módulo

### `src/config.py` — configuración única
- Carga `.env` (con `python-dotenv`). **Ningún otro archivo lee `os.environ`.**
- Valida que las variables obligatorias existan y da mensajes claros si faltan.
- Configura el **logging**: consola + archivo rotatorio `logs/agente.log`.
- Fábricas cacheadas:
  - `get_llm(reasoning=True/False)` → cliente de chat (`ChatOpenAI` apuntando a
    OpenRouter / DeepSeek / OpenAI / Ollama). Con `reasoning=False` pide al
    proveedor que no emita cadena de pensamiento (llamadas de explicar/clasificar
    /sintetizar: más rápidas y concisas).
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
  `TOP n` tras el `SELECT`. Excepción: los agregados globales
  (`SELECT COUNT(*) ...` sin `GROUP BY`) se dejan tal cual.
- `log_query(sql, source)`: deja constancia de **toda** consulta que se va a
  ejecutar.
- `looks_like_write_request(pregunta)`: heurística **conservadora** sobre la
  pregunta del usuario (verbo destructivo al inicio: *borra*, *elimina*,
  *actualiza … a …*; o SQL literal `DELETE FROM`, `UPDATE … SET`, …). Si acierta,
  el agente responde el bloqueo **sin gastar ni una llamada al LLM**. Ante la
  duda devuelve `False` y siguen protegiendo las demás capas.

### `src/sql_agent.py` — Text-to-SQL (2 llamadas, sin ReAct)
- `solve_sql(pregunta)`:
  1. **1 llamada al LLM** para generar el `SELECT`. El esquema de las tablas
     permitidas va **fijo en el prompt** (`_schema_info()`, cacheado), así el
     modelo no necesita "herramientas" para explorarlo → menos llamadas.
  2. `guardrails.validate_select_only` + `enforce_row_limit` + `log_query`.
  3. Ejecuta contra la BD con el usuario de solo lectura. Si falla, **un**
     reintento pasándole el error al LLM.
- `explain_messages(...)`: prompt de la **2.ª llamada** (explicar el resultado),
  con reglas de estilo estrictas: responder solo lo que se pregunta, sin
  secciones de análisis/observaciones/recomendaciones no pedidas, sin emojis.
- `answer_sql()` (sin streaming, para tests/CLI) nunca lanza: los errores van en
  `error`. En la app, el router hace la 2.ª llamada en streaming.
- Intento de escritura (`borra`, `update`, …): se corta en el paso 2 y se
  devuelve `BLOCKED_MSG` **sin gastar la llamada de explicación**.

### `src/ingest.py` — indexado del RAG (se ejecuta a mano)
- Lee `data/docs/` (`.pdf`, `.docx`, `.txt`, `.md`), guarda el nombre de archivo
  en el metadato `source`.
- Trocea con `RecursiveCharacterTextSplitter` (~3200 caracteres, solape ~400).
- Calcula embeddings y guarda los vectores en **Chroma** (`data/chroma_db/`).
- `python -m src.ingest` reconstruye el índice; `--append` añade sin borrar.
- **No** se ejecuta al arrancar la app: solo cuando cambian los documentos.

### `src/rag_agent.py` — RAG
- `retrieve(pregunta)` → `(contexto, fuentes)` desde Chroma (top-k = 4, ajustado
  a la baja si hay menos fragmentos). No gasta llamada al LLM.
- `answer_messages(...)` → prompt estricto: *responde solo con el contexto; si no
  está, di "No tengo esa información en los documentos disponibles"; cita el
  archivo; sé breve y sin análisis no pedido*.
- `answer_rag()` (sin streaming) devuelve `answer` + `sources` + `error`.

### `src/router.py` — orquestador (Python puro)
- `_classify(pregunta)`:
  - Primero, **heurística de palabras clave**: si solo hay señales de datos →
    `sql`; si solo hay señales documentales → `rag` (sin llamada al LLM).
  - Si hay ambas o ninguna → **1 llamada al LLM** (prompt con ejemplos few-shot,
    `max_tokens=8`). Ante fallo, cae en `mixta`/`sql`.
- `prepare(pregunta)` → `(meta, tokens)`: ejecuta la parte lenta (clasificar +
  SQL/recuperar) y devuelve un **generador** que emite la respuesta final en
  streaming (explicar / responder / sintetizar según la ruta).
- `run_router(pregunta)` → versión sin streaming (consume el generador). Es lo
  que usan los tests y la CLI. Ninguna de las dos lanza excepción.
- Ya **no usa LangGraph**: el flujo es lineal y cabe en `prepare()`.

### `app.py` — interfaz
- Chat de Streamlit con historial en memoria de sesión.
- Spinner "Consultando..." mientras `prepare()` clasifica y consulta; después la
  respuesta se **escribe en streaming** (`st.write_stream`), palabra a palabra.
- Cada respuesta trae un desplegable con la ruta, el SQL y el documento fuente.
- Barra lateral con la configuración activa (sin secretos) y botón de limpiar.

---

## 5. Los guardarraíles de seguridad

Por qué son el corazón de la PoC: un modelo de lenguaje **puede** generar SQL
destructivo (por error o inducido). La defensa es **en capas**:

| Capa | Qué impide | Dónde |
|---|---|---|
| Usuario `agente_readonly` con rol `db_datareader` y `DENY` de escritura | Que *cualquier* cosa que no sea lectura llegue a ejecutarse | `sql/01_crear_usuario_readonly.sql` |
| `looks_like_write_request` | Que una pregunta con intención de escritura evidente llegue siquiera a generar SQL | `src/guardrails.py` |
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
| LLM por API (OpenRouter/DeepSeek/OpenAI) | Modelo local | PoC en un PC; sin GPU potente. La API es barata y suficiente. |
| Embeddings **locales** por defecto | Embeddings por API | Coste cero y sin enviar los documentos a un tercero. |
| SQL en **2 llamadas** (generar + explicar), esquema fijo en el prompt | Agente **ReAct** con herramientas (`list_tables`/`describe_tables`/…) | El ReAct hacía 4-5 llamadas encadenadas → lento. Con el esquema en el prompt bastan 2. Se pierde algo de flexibilidad ante preguntas muy raras. |
| Router en **Python plano** con heurística + 1 llamada | LangGraph | Flujo lineal; menos dependencias, arranque más rápido, sin llamada de clasificación cuando las palabras clave son claras. |
| Respuesta final **en streaming** | Esperar y mostrarla entera | Con modelos lentos, ver texto en 1-2 s en vez de un spinner de 20 s. |
| Prompts con "responde solo lo que se pregunta, sin análisis no pedido" | Dejar al modelo divagar | Respuestas más útiles y más cortas (menos tokens = menos coste y latencia). |
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
  `sql`. Se corrige afinando la heurística de palabras clave o los ejemplos del
  prompt del clasificador.
- **Coste y latencia.** Cada pregunta son 2-3 llamadas al LLM. Con modelos
  `:free` de OpenRouter cada llamada tarda 15-40 s (infra compartida); con un
  modelo de pago rápido (`gpt-4o-mini`, `deepseek-chat`) baja a 1-3 s.
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
- **Caché** de preguntas frecuentes (antes de llamar al LLM).
- **Métricas**: registrar acierto de ruta, latencia y coste por pregunta.
- **Más fuentes**: un CRM, una hoja de cálculo, una API — nuevas ramas en `prepare()`.
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
  herramientas* en bucle. Esta PoC lo **sustituyó** por 2 llamadas fijas
  (generar SQL + explicar) para ir más rápido.
- **Streaming**: recibir y mostrar la respuesta del LLM token a token, en vez de
  esperar a que termine.
- **Router / orquestador**: la lógica que decide qué agente atiende cada
  pregunta.
- **Guardrail**: validación que impide que el sistema haga algo no permitido.
