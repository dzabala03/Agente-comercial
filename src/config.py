"""
config.py
=========
Punto único de configuración del proyecto.

Responsabilidades:
  * Cargar las variables de entorno desde .env
  * Exponerlas como constantes tipadas y validadas
  * Configurar el logging (consola + archivo rotatorio en logs/agente.log)
  * Fábricas (`get_*`) para los objetos compartidos: LLM, embeddings,
    engine de SQLAlchemy y wrapper SQLDatabase de LangChain.

NINGÚN otro módulo debe leer os.environ directamente.
"""
from __future__ import annotations

import logging
import os
import sys
import urllib.parse
import warnings
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
#  Silenciar ruido de librerías de terceros (antes de importarlas)
# --------------------------------------------------------------------------
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")   # Chroma
os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

warnings.filterwarnings("ignore", message=r".*was not located in columns.*")
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")

# Consola en UTF-8 (Windows abre cp1252 por defecto y las respuestas del LLM
# suelen traer acentos o emojis que romperían el print).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --------------------------------------------------------------------------
#  Carga del .env
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _req(name: str) -> str:
    """Devuelve una variable de entorno obligatoria o aborta con mensaje claro."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno obligatoria '{name}'. "
            f"Revisa tu archivo .env (usa .env.example como plantilla)."
        )
    return value


def _opt(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --------------------------------------------------------------------------
#  LLM
# --------------------------------------------------------------------------
LLM_PROVIDER = _opt("LLM_PROVIDER", "deepseek").lower()
LLM_MODEL = _opt("LLM_MODEL", "deepseek-chat")

# Azure OpenAI no habla el protocolo "base_url estilo OpenAI": usa su propio
# cliente (endpoint del recurso + api-version + nombre del deployment).
# Aquí LLM_MODEL = nombre del DEPLOYMENT en Azure (no el nombre del modelo base).
AZURE_OPENAI_ENDPOINT = _opt("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = _opt("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# base_url según proveedor (todos hablan el protocolo estilo OpenAI)
_LLM_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": None,  # SDK usa el endpoint por defecto
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
if LLM_PROVIDER != "azure" and LLM_PROVIDER not in _LLM_BASE_URLS:
    raise RuntimeError(
        f"LLM_PROVIDER='{LLM_PROVIDER}' no soportado. "
        f"Usa uno de: azure, {', '.join(_LLM_BASE_URLS)}."
    )
# LLM_BASE_URL del .env tiene prioridad (permite endpoints self-host o proxys).
LLM_BASE_URL = _opt("LLM_BASE_URL") or _LLM_BASE_URLS.get(LLM_PROVIDER)

# --------------------------------------------------------------------------
#  Embeddings
# --------------------------------------------------------------------------
EMBEDDING_PROVIDER = _opt("EMBEDDING_PROVIDER", "local").lower()
# Modelo multilingüe AJUSTADO PARA RECUPERACIÓN (no solo similitud): con mpnet
# el PDF de privacidad puntuaba más alto que el documento correcto; e5 separa
# bien español. ~2,2 GB, se descarga una vez; el corpus es pequeño.
LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# --------------------------------------------------------------------------
#  SQL Server
# --------------------------------------------------------------------------
SQL_SERVER_HOST = _opt("SQL_SERVER_HOST", "localhost")
SQL_SERVER_PORT = _opt("SQL_SERVER_PORT", "1433")
SQL_SERVER_DB = _opt("SQL_SERVER_DB", "AdventureWorksLT")
SQL_SERVER_USER = _opt("SQL_SERVER_USER", "agente_readonly")
SQL_ODBC_DRIVER = _opt("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")

# Tablas del esquema SalesLT que el agente PUEDE ver. Limitar la superficie
# reduce alucinaciones y evita exponer tablas irrelevantes.
SQL_SCHEMA = "SalesLT"
SQL_INCLUDE_TABLES = [
    "Customer",
    "Address",
    "CustomerAddress",
    "Product",
    "ProductCategory",
    "ProductModel",
    "SalesOrderHeader",
    "SalesOrderDetail",
]

# --------------------------------------------------------------------------
#  Guardarraíles / límites
# --------------------------------------------------------------------------
SQL_MAX_ROWS = int(_opt("SQL_MAX_ROWS", "100"))
SQL_QUERY_TIMEOUT = int(_opt("SQL_QUERY_TIMEOUT", "10"))

# --------------------------------------------------------------------------
#  Vector store
# --------------------------------------------------------------------------
CHROMA_PERSIST_DIR = str(
    (PROJECT_ROOT / _opt("CHROMA_PERSIST_DIR", "./data/chroma_db")).resolve()
)
DOCS_DIR = str((PROJECT_ROOT / "data" / "docs").resolve())
CHROMA_COLLECTION = "documentos_comerciales"
# Distancia coseno: separa mucho mejor que L2 con embeddings de tipo
# sentence-transformers y permite un umbral de relevancia absoluto estable.
CHROMA_COLLECTION_METADATA = {"hnsw:space": "cosine"}

# --------------------------------------------------------------------------
#  Logging
# --------------------------------------------------------------------------
LOG_LEVEL = _opt("LOG_LEVEL", "INFO").upper()
LOG_FILE = str((PROJECT_ROOT / _opt("LOG_FILE", "./logs/agente.log")).resolve())

_LOGGING_READY = False


def setup_logging() -> None:
    """Configura el logging raíz una sola vez (idempotente)."""
    global _LOGGING_READY
    if _LOGGING_READY:
        return

    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Bajar ruido de librerías de terceros
    for noisy in ("httpx", "urllib3", "openai", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Chroma emite errores de telemetría inofensivos (bug conocido de posthog)
    logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)

    _LOGGING_READY = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


# --------------------------------------------------------------------------
#  Fábricas de objetos compartidos
# --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def get_llm(temperature: float = 0.0, reasoning: bool = True):
    """
    Cliente de chat LangChain para el proveedor elegido.

    reasoning=False  -> pide al proveedor que NO emita cadena de pensamiento.
      Se usa en las llamadas de "explicar / clasificar / sintetizar", donde no
      hace falta razonar y sí interesa que sea rápido y conciso. Solo tiene
      efecto en OpenRouter (parámetro unificado 'reasoning').
    """
    if LLM_PROVIDER == "azure":
        from langchain_openai import AzureChatOpenAI

        if not AZURE_OPENAI_ENDPOINT:
            raise RuntimeError(
                "LLM_PROVIDER=azure requiere AZURE_OPENAI_ENDPOINT en .env "
                "(p.ej. https://<tu-recurso>.openai.azure.com/)."
            )
        return AzureChatOpenAI(
            azure_deployment=LLM_MODEL,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
            api_key=_req("LLM_API_KEY"),
            temperature=temperature,
            timeout=60,
            max_retries=2,
        )

    from langchain_openai import ChatOpenAI

    default_headers: dict | None = None
    extra_body: dict | None = None
    if LLM_PROVIDER == "openrouter":
        default_headers = {
            "HTTP-Referer": _opt("OPENROUTER_APP_URL", "http://localhost:8501"),
            "X-Title": _opt("OPENROUTER_APP_TITLE", "Agente Comercial PoC"),
        }
        if not reasoning:
            extra_body = {"reasoning": {"enabled": False}}

    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=_req("LLM_API_KEY"),
        base_url=LLM_BASE_URL,
        temperature=temperature,
        timeout=60,
        max_retries=2,
        default_headers=default_headers,
        extra_body=extra_body,
    )


@lru_cache(maxsize=None)
def get_embeddings():
    """Devuelve el objeto de embeddings según EMBEDDING_PROVIDER."""
    if EMBEDDING_PROVIDER == "openai":
        from langchain_openai import OpenAIEmbeddings

        api_key = _opt("OPENAI_API_KEY") or (
            _opt("LLM_API_KEY") if LLM_PROVIDER == "openai" else ""
        )
        if not api_key:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai pero no hay OPENAI_API_KEY "
                "(ni LLM_API_KEY con LLM_PROVIDER=openai)."
            )
        return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL, api_key=api_key)

    # Por defecto: local, sin coste, se ejecuta en CPU.
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings

    return FastEmbedEmbeddings(model_name=LOCAL_EMBEDDING_MODEL)


def _odbc_connection_uri() -> str:
    """Construye la URI SQLAlchemy (mssql+pyodbc) hacia el usuario de solo lectura."""
    password = _req("SQL_SERVER_PASSWORD")
    odbc_str = (
        f"DRIVER={{{SQL_ODBC_DRIVER}}};"
        f"SERVER={SQL_SERVER_HOST},{SQL_SERVER_PORT};"
        f"DATABASE={SQL_SERVER_DB};"
        f"UID={SQL_SERVER_USER};"
        f"PWD={password};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
    )
    return "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc_str)


@lru_cache(maxsize=None)
def get_sql_engine():
    """
    Engine de SQLAlchemy con:
      * pool pequeño (PoC de un solo usuario)
      * timeout de consulta a nivel de driver pyodbc
    """
    from sqlalchemy import create_engine, event

    engine = create_engine(
        _odbc_connection_uri(),
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_query_timeout(dbapi_connection, _connection_record):  # noqa: ANN001
        # pyodbc: .timeout = segundos máximos por consulta (0 = sin límite)
        try:
            dbapi_connection.timeout = SQL_QUERY_TIMEOUT
        except Exception:  # pragma: no cover - driver sin soporte
            pass

    return engine


@lru_cache(maxsize=None)
def get_sql_database():
    """Wrapper SQLDatabase de LangChain, restringido al esquema y tablas permitidos."""
    from langchain_community.utilities import SQLDatabase

    return SQLDatabase(
        engine=get_sql_engine(),
        schema=SQL_SCHEMA,
        include_tables=SQL_INCLUDE_TABLES,
        sample_rows_in_table_info=2,
        max_string_length=300,
    )


def describe_runtime() -> dict:
    """Resumen de configuración activa (para mostrar en la UI / logs, sin secretos)."""
    return {
        "LLM_PROVIDER": LLM_PROVIDER,
        "LLM_MODEL": LLM_MODEL,
        "LLM_BASE_URL": LLM_BASE_URL or AZURE_OPENAI_ENDPOINT or "(por defecto del SDK)",
        "EMBEDDING_PROVIDER": EMBEDDING_PROVIDER,
        "SQL_SERVER": f"{SQL_SERVER_HOST}:{SQL_SERVER_PORT}/{SQL_SERVER_DB}",
        "SQL_SERVER_USER": SQL_SERVER_USER,
        "SQL_MAX_ROWS": SQL_MAX_ROWS,
        "SQL_QUERY_TIMEOUT": SQL_QUERY_TIMEOUT,
        "CHROMA_PERSIST_DIR": CHROMA_PERSIST_DIR,
    }
