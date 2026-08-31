# ============================================================================
#  run_lan.ps1
#  Arranca la app accesible desde otros dispositivos de la MISMA red Wi-Fi
#  (tu móvil, portátil, etc.).
#
#  Uso:   ./scripts/run_lan.ps1
#  La primera vez pedirá permiso de administrador para abrir el puerto 8501
#  en el Firewall de Windows (solo esa vez).
#
#  AVISO DE SEGURIDAD: la app NO tiene login. Cualquiera en tu red Wi-Fi que
#  abra la URL puede usar el agente y consumir tu cuota de API. No la expongas
#  a Internet sin autenticación.
# ============================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$port = 8501
$ruleName = "Agente Comercial Streamlit ($port)"

# --- Regla de firewall (requiere admin; se crea una sola vez) ---------------
$exists = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $exists) {
    Write-Host "==> Creando regla de Firewall para el puerto $port (pide admin)..."
    Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
        "-NoProfile","-Command",
        "New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Private"
    )
}

# --- IP de esta máquina en la Wi-Fi ---------------------------------------
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.InterfaceAlias -match 'Wi-?Fi' -and $_.PrefixOrigin -eq 'Dhcp' } |
       Select-Object -First 1).IPAddress
if (-not $ip) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'WSL|vEthernet' } |
           Select-Object -First 1).IPAddress
}

Write-Host ""
Write-Host "======================================================"
Write-Host "  En el móvil (misma red Wi-Fi que este PC), abre:"
Write-Host "     http://$($ip):$port"
Write-Host "======================================================"
Write-Host "  Deja esta ventana abierta. Ctrl+C para parar."
Write-Host ""

& (Join-Path $root "venv\Scripts\python.exe") -m streamlit run app.py `
    --server.address 0.0.0.0 --server.port $port
