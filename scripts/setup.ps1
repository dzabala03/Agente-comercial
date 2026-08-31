# ============================================================================
#  setup.ps1
#  Prepara el entorno Python: crea el venv e instala dependencias.
#  Uso (desde la raíz del proyecto):   ./scripts/setup.ps1
#  Si PowerShell bloquea el script:    Set-ExecutionPolicy -Scope Process RemoteSigned
# ============================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = "python"
& $py --version

if (-not (Test-Path (Join-Path $root "venv"))) {
    Write-Host "==> Creando entorno virtual en .\venv"
    & $py -m venv venv
}

$venvPy = Join-Path $root "venv\Scripts\python.exe"
Write-Host "==> Actualizando pip"
& $venvPy -m pip install --upgrade pip

Write-Host "==> Instalando dependencias (requirements.txt)"
& $venvPy -m pip install -r (Join-Path $root "requirements.txt")

Write-Host "==> Instalando dependencias de desarrollo (requirements-dev.txt)"
& $venvPy -m pip install -r (Join-Path $root "requirements-dev.txt")

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "==> Se creó .env desde la plantilla. EDÍTALO con tus credenciales."
}

Write-Host ""
Write-Host "Entorno listo. Actívalo con:  .\venv\Scripts\Activate.ps1"
Write-Host "Luego:  python -m src.ingest   y   streamlit run app.py"
