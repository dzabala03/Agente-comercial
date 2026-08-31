# ============================================================================
#  restore_db.ps1
#  Restaura AdventureWorksLT dentro del contenedor SQL Server de docker-compose.
#
#  Requisitos previos:
#    1. 'docker compose up -d' ya ejecutado y contenedor "healthy".
#    2. El archivo AdventureWorksLT.bak copiado en  .\data\backups\
#       (descárgalo de: https://learn.microsoft.com/sql/samples/adventureworks-install-configure)
#    3. Variable MSSQL_SA_PASSWORD definida en .env
#
#  Uso:   ./scripts/restore_db.ps1
# ============================================================================
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$bakLocal = Join-Path $root "data\backups\AdventureWorksLT.bak"
$container = "agente_sqlserver"
$dbName = "AdventureWorksLT"

if (-not (Test-Path $envFile)) { throw "No existe .env. Copia .env.example a .env primero." }
if (-not (Test-Path $bakLocal)) { throw "No se encuentra $bakLocal. Descarga el .bak y colócalo ahí." }

# Leer MSSQL_SA_PASSWORD del .env
$saPwd = (Get-Content $envFile | Where-Object { $_ -match '^\s*MSSQL_SA_PASSWORD\s*=' } |
          Select-Object -First 1) -replace '^\s*MSSQL_SA_PASSWORD\s*=\s*', ''
if ([string]::IsNullOrWhiteSpace($saPwd)) { throw "MSSQL_SA_PASSWORD vacío en .env" }

$sqlcmd = "/opt/mssql-tools18/bin/sqlcmd"
$bakInContainer = "/var/opt/mssql/backups/AdventureWorksLT.bak"

Write-Host "==> Verificando que el contenedor responde..."
docker exec $container $sqlcmd -S localhost -U sa -P $saPwd -C -Q "SELECT @@VERSION" | Out-Host

Write-Host "==> Leyendo nombres lógicos del backup..."
docker exec $container $sqlcmd -S localhost -U sa -P $saPwd -C -Q `
  "RESTORE FILELISTONLY FROM DISK = N'$bakInContainer'" | Out-Host

Write-Host "==> Restaurando $dbName ..."
$restore = @"
RESTORE DATABASE [$dbName] FROM DISK = N'$bakInContainer'
WITH MOVE 'AdventureWorksLT2022_Data' TO '/var/opt/mssql/data/$dbName.mdf',
     MOVE 'AdventureWorksLT2022_Log'  TO '/var/opt/mssql/data/$dbName`_log.ldf',
     REPLACE, RECOVERY;
"@
docker exec $container $sqlcmd -S localhost -U sa -P $saPwd -C -Q $restore | Out-Host

Write-Host "==> Comprobando..."
docker exec $container $sqlcmd -S localhost -U sa -P $saPwd -C -d $dbName -Q `
  "SELECT COUNT(*) AS clientes FROM SalesLT.Customer" | Out-Host

Write-Host ""
Write-Host "Si los nombres lógicos MOVE fallan, mira la salida de RESTORE FILELISTONLY"
Write-Host "y ajusta los nombres 'AdventureWorksLT2022_Data' / '_Log' en este script."
Write-Host "Listo. Ahora ejecuta sql/01_crear_usuario_readonly.sql como 'sa'."
