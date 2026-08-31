# Puesta en marcha — qué tienes que hacer tú (David) y cómo

Este documento es la guía operativa. Sigue los bloques en orden. Los comandos
son para **PowerShell en Windows**, ejecutados **desde la raíz del proyecto**
(`C:\Users\david\OneDrive\Documentos\Proyectos\Agente`).

Leyenda:
- 🧑‍💻 = lo haces tú a mano (instalar, crear cuentas, pegar claves).
- ⚙️ = lo hace un script/comando del repo.
- ✅ = comprobación para saber que ese bloque quedó bien.

---

## 0. Subir el proyecto a tu repositorio de GitHub

El repo destino es **https://github.com/dzabala03/Agente-comercial.git**.
Créalo vacío en GitHub (sin README, sin .gitignore) y luego:

```powershell
cd "C:\Users\david\OneDrive\Documentos\Proyectos\Agente"
git init
git add .
git commit -m "PoC agente RAG + SQL: estructura inicial y código base"
git branch -M main
git remote add origin https://github.com/dzabala03/Agente-comercial.git
git push -u origin main
```

✅ En GitHub deben aparecer todos los archivos **menos** `.env` (todavía no
existe) y `venv/` (no se sube). El archivo `.env.example` **sí** se sube.

> A partir de aquí, cada avance: `git add -A; git commit -m "..."; git push`.
> Trabaja en ramas para cambios grandes: `git checkout -b fase-3-sql-agent`.

---

## 1. Prerrequisitos (instalar una sola vez) 🧑‍💻

| Herramienta | Cómo | Comprobación |
|---|---|---|
| **Python 3.11+** | https://www.python.org/downloads/ (marca "Add python to PATH") | `python --version` |
| **Docker Desktop** | https://www.docker.com/products/docker-desktop/ — ábrelo y espera a que diga "running" | `docker version` |
| **ODBC Driver 18 for SQL Server** | https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server | Panel de control → "Orígenes de datos ODBC (64 bits)" → pestaña "Controladores" → debe figurar |
| **Git** | https://git-scm.com/download/win | `git --version` |
| **VS Code** (recomendado) | https://code.visualstudio.com/ | — |
| **Cliente SQL: DBeaver Community** | https://dbeaver.io/download/  (`winget install DBeaver.DBeaver.Community`) | abre y conecta más adelante |

> Nota: Azure Data Studio fue retirado por Microsoft (fin de soporte feb-2026).
> Usamos **DBeaver Community**. La primera vez que conectes a SQL Server, DBeaver
> te ofrecerá descargar el driver JDBC de MSSQL automáticamente: acepta.
>
> Instalación rápida de todo con winget (PowerShell):
> ```powershell
> winget install --id Python.Python.3.11 -e
> winget install --id Docker.DockerDesktop -e
> winget install --id Microsoft.msodbcsql.18 -e
> winget install --id Git.Git -e
> winget install --id Microsoft.VisualStudioCode -e
> winget install --id DBeaver.DBeaver.Community -e
> ```

### Cuenta y API key del LLM 🧑‍💻

Elige **uno**:

- **OpenRouter** (gratis con modelos `:free`): https://openrouter.ai/ → "Keys" →
  crea una (empieza por `sk-or-v1-...`). Modelo: `deepseek/deepseek-chat-v3-0324:free`
  (verifica el slug vigente en https://openrouter.ai/models?q=deepseek+free).
  Límite: ~20 req/min y un tope diario; suficiente para un usuario probando.
- **DeepSeek** (de pago pero céntimos): https://platform.deepseek.com/ → "API Keys".
  Modelo: `deepseek-chat`.
- **OpenAI**: https://platform.openai.com/ → "API Keys". Modelo: `gpt-4o-mini`.

Guarda la clave, la pegarás en `.env` en el paso 4.

✅ `python --version` devuelve 3.11 o superior y `docker version` no da error.

---

## 2. Base de datos SQL Server (contenedor)

### 2.1 Crear el `.env` mínimo para Docker ⚙️🧑‍💻

```powershell
Copy-Item .env.example .env
notepad .env
```

Por ahora solo necesitas rellenar **`MSSQL_SA_PASSWORD`** (contraseña del
administrador del contenedor). Requisito: 8+ caracteres con mayúscula, minúscula,
número y símbolo. Ejemplo: `Agente_SqlServer_2026!`. Guarda y cierra.

### 2.2 Levantar el contenedor ⚙️

```powershell
docker compose up -d
docker compose ps
```

✅ `docker compose ps` muestra el servicio `sqlserver` como `running` y, tras
~30-60 s, `healthy`.

### 2.3 Descargar la base de muestra 🧑‍💻

Descarga el `.bak` de **AdventureWorksLT** desde:
https://learn.microsoft.com/sql/samples/adventureworks-install-configure
(sección "Download backup files").

Cópialo a la carpeta del repo con **este nombre exacto**:

```powershell
Copy-Item "$HOME\Downloads\AdventureWorksLT*.bak" ".\data\backups\AdventureWorksLT.bak"
```

> El `.bak` que publica Microsoft ahora está tomado en **SQL Server 2025**, por eso
> `docker-compose.yml` usa la imagen `mssql/server:2025-latest`. Un `.bak` no se
> puede restaurar en una versión de SQL Server anterior a la que lo generó.

### 2.4 Restaurar la base ⚙️

```powershell
# Si PowerShell bloquea scripts en esta sesión:
Set-ExecutionPolicy -Scope Process RemoteSigned
./scripts/restore_db.ps1
```

El script lee los nombres lógicos del `.bak`, restaura la base como
`AdventureWorksLT` y cuenta los clientes.

> Si falla en el `RESTORE` por los nombres lógicos (`MOVE ...`), copia la salida
> de `RESTORE FILELISTONLY` que imprime el script y ajusta en
> `scripts/restore_db.ps1` los nombres `AdventureWorksLT2022_Data` / `_Log`.

✅ El script imprime al final un número de `clientes` > 0.

### 2.5 Crear el usuario de solo lectura ⚙️🧑‍💻

1. Abre **DBeaver** → nueva conexión → **SQL Server** y conéctate:
   - Host: `localhost`  ·  Puerto: `1433`  ·  Usuario: `sa`  ·  Contraseña: la de `MSSQL_SA_PASSWORD`
   - En la pestaña de propiedades del driver marca **`trustServerCertificate = true`**
     (o Encrypt = false). Acepta la descarga del driver JDBC si te la ofrece.
   - Abre un editor SQL sobre esa conexión (icono "SQL" o Ctrl+]).
2. Abre `sql/01_crear_usuario_readonly.sql`, **cambia** `CAMBIA_esta_password_1!`
   por la misma contraseña que pusiste en `.env` → `SQL_SERVER_PASSWORD`, y
   ejecútalo (F5 por lote, o Alt+X todo el script).
   - Alternativa sin GUI: `docker exec -i agente_sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "<MSSQL_SA_PASSWORD>" -C < sql/01_crear_usuario_readonly.sql`
3. Verifica: reconéctate como `agente_readonly` y ejecuta `sql/02_smoke_test.sql`.
   Las tres primeras consultas funcionan; si descomentas el `UPDATE` del final,
   **debe** dar `The UPDATE permission was denied`.

> Importante: **no** añadas `DENY CONTROL` a nivel de base de datos al usuario de
> solo lectura. `CONTROL` es un permiso paraguas y un `DENY CONTROL` impide
> incluso abrir la base (`Cannot open database ... requested by the login`).
> `db_datareader` + `DENY INSERT, UPDATE, DELETE, EXECUTE` es suficiente.

✅ Puedes ejecutar un `SELECT` como `agente_readonly` y el `UPDATE` te lo deniega.

---

## 3. Entorno Python ⚙️

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned   # si hace falta
./scripts/setup.ps1
```

Esto crea `.\venv`, actualiza `pip` e instala `requirements.txt` +
`requirements-dev.txt`.

> Si `pyodbc` falla al compilar: instala "Microsoft C++ Build Tools"
> (https://visualstudio.microsoft.com/visual-cpp-build-tools/) y repite.
> Si el resolver de `pip` da un conflicto de versiones imposible: edita
> `requirements.txt` quitando los `~=` de las líneas `langchain*` y vuelve a
> `pip install -r requirements.txt`; anota en el README qué versiones quedaron.

Activa el entorno (en cada terminal nueva):

```powershell
.\venv\Scripts\Activate.ps1
```

✅ El prompt muestra `(venv)` y `python -c "import langchain, streamlit, pyodbc, chromadb"` no da error.

---

## 4. Completar el `.env` 🧑‍💻

```powershell
notepad .env
```

Rellena:

| Variable | Valor (ejemplo con OpenRouter) |
|---|---|
| `LLM_PROVIDER` | `openrouter` |
| `LLM_API_KEY` | tu clave `sk-or-v1-...` del paso 1 |
| `LLM_MODEL` | `minimax/minimax-m3:free` (probado OK con este proyecto) |
| `EMBEDDING_PROVIDER` | `local` (obligatorio con OpenRouter; no da embeddings) |
| `SQL_SERVER_PASSWORD` | la contraseña de `agente_readonly` (paso 2.5) |
| resto de `SQL_SERVER_*` | ya vienen bien por defecto |

Los modelos `:free` de OpenRouter **rotan**: si ves un 404 *"unavailable for free"*
o errores 429 de saturación, elige otro slug gratis (que soporte *tools*) en
https://openrouter.ai/models?order=pricing-low-to-high&max_price=0 y actualiza
`LLM_MODEL`. Para listar los que hay ahora mismo:

```powershell
.\venv\Scripts\python.exe -c "import json,urllib.request as u; from src.config import _opt; d=json.load(u.urlopen(u.Request('https://openrouter.ai/api/v1/models',headers={'Authorization':'Bearer '+_opt('LLM_API_KEY')})))['data']; [print(m['id']) for m in d if str(m['pricing']['prompt']) in ('0','0.0')]"
```

✅ Comprobación de configuración:

```powershell
python -c "from src.config import describe_runtime; print(describe_runtime())"
```

Debe imprimir el diccionario sin lanzar excepción.

---

## 5. Indexar los documentos del RAG ⚙️

```powershell
python -m src.ingest
```

La primera vez, con `EMBEDDING_PROVIDER=local`, descarga el modelo de embeddings
(~130 MB). Al terminar imprime `OK: N fragmentos indexados`.

> Repite este comando **cada vez** que añadas o edites archivos en `data/docs/`.

✅ Prueba directa del RAG:

```powershell
python -m src.rag_agent "¿Cuál es la política de devoluciones?"
```

Debe responder citando `politica-devoluciones-y-garantia.txt`.

---

## 6. Probar el agente SQL por separado ⚙️

```powershell
python -m src.sql_agent "¿Cuántos clientes hay en la base de datos?"
python -m src.sql_agent "¿Cuáles son los 5 clientes con mayor importe de compras?"
python -m src.sql_agent "Borra el cliente con ID 1"
```

✅ Las dos primeras dan una respuesta en lenguaje natural y muestran el SQL
ejecutado. La tercera **no** ejecuta nada: responde que está bloqueado.
Revisa `logs/agente.log`: cada `SELECT` queda registrado.

---

## 7. Arrancar la aplicación ⚙️

```powershell
streamlit run app.py
```

Se abre http://localhost:8501. En la barra lateral ves la configuración activa.
Prueba una pregunta de datos, una de documentos y una mixta.

✅ Respuesta correcta de principio a fin, con el desplegable "Detalle" mostrando
la ruta, el SQL y/o el documento citado.

---

## 8. Validación integral

```powershell
pytest
```

✅ Todas las pruebas de `tests/test_sql_agent.py` en verde (guardrails).

Luego abre `tests/preguntas_prueba.md` y ejecuta las 20 preguntas en la app,
rellenando la tabla. Criterio de aceptación de la PoC:

- Ruta correcta en ≥ 18/20.
- 0 ejecuciones de escritura (100 % de los intentos bloqueados).
- El RAG admite "no tengo esa información" cuando corresponde.

Ajusta los prompts (`SYSTEM_PROMPT` en `src/sql_agent.py`, `_CLASSIFIER_PROMPT`
en `src/router.py`, el `system` de `src/rag_agent.py`) según los fallos y repite.

---

## 9. Uso diario (después de la instalación)

```powershell
cd "C:\Users\david\OneDrive\Documentos\Proyectos\Agente"
docker compose up -d                 # si el contenedor no está arrancado
.\venv\Scripts\Activate.ps1
streamlit run app.py
```

Para parar: `Ctrl+C` en la terminal de Streamlit y, opcionalmente,
`docker compose stop`.

---

## Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Falta la variable de entorno obligatoria 'LLM_API_KEY'` | `.env` incompleto | Rellena la clave y reinicia el proceso |
| `Login failed for user 'agente_readonly'` | contraseña mal en `.env` o usuario no creado | Repite paso 2.5; revisa `SQL_SERVER_PASSWORD` |
| `Can't open lib 'ODBC Driver 18 for SQL Server'` | driver ODBC no instalado o nombre distinto | Instálalo; ajusta `SQL_ODBC_DRIVER` en `.env` |
| `pyodbc ... Build Tools` al instalar | falta compilador C++ | Instala "C++ Build Tools" y reinstala |
| El contenedor no pasa a `healthy` | contraseña `sa` no cumple política | Cambia `MSSQL_SA_PASSWORD`, `docker compose down -v` y `up -d` |
| RAG responde "No tengo esa información" siempre | no ejecutaste `python -m src.ingest`, o el índice quedó vacío | Reindexa |
| Respuestas SQL lentas | el agente ReAct hace varias llamadas al LLM | normal en la PoC; usa un modelo rápido (`deepseek-chat` / `gpt-4o-mini`) |
