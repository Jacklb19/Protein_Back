param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin', [int]$Port = 55441,
      [Parameter(ValueFromRemainingArguments = $true)][string[]]$AuditArguments)
# Load audit against a disposable loopback cluster with every migration and RLS policy applied.
# Arguments after the named ones go to scripts.audit_local_api (for example --seconds 3 --port 5296).
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$database = 'pf_load_audit'
$clusterPath = Join-Path $repository ('tmp\pg-load-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $clusterPath -Force | Out-Null
$databaseUrl = "postgresql://postgres@127.0.0.1:$Port/$database"
function Invoke-Pg([string]$Executable, [string[]]$Arguments) {
  & (Join-Path $PostgresBin $Executable) @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}
$started = $false
try {
  Invoke-Pg 'initdb.exe' @('-D', $clusterPath, '-U', 'postgres', '-A', 'trust', '--encoding=UTF8', '--locale=C')
  Invoke-Pg 'pg_ctl.exe' @('-D', $clusterPath, '-l', (Join-Path $clusterPath 'server.log'), '-o', "-h 127.0.0.1 -p $Port", '-w', 'start')
  $started = $true
  Invoke-Pg 'createdb.exe' @('-h', '127.0.0.1', '-p', "$Port", '-U', 'postgres', $database)
  Invoke-Pg 'psql.exe' @($databaseUrl, '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', (Join-Path $repository 'tests\postgres_bootstrap.sql'))
  foreach ($migration in Get-ChildItem -LiteralPath (Join-Path $repository 'supabase\migrations') -Filter '*.sql' | Sort-Object Name) {
    Invoke-Pg 'psql.exe' @($databaseUrl, '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', $migration.FullName)
  }
  $env:PF_AUDIT_DATABASE_URL = $databaseUrl
  $env:PF_PSQL = Join-Path $PostgresBin 'psql.exe'
  & (Join-Path $repository '.venv\Scripts\python.exe') -m scripts.audit_local_api --storage postgres @AuditArguments
  if ($LASTEXITCODE -ne 0) { throw 'Load audit against local PostgreSQL failed' }
} finally {
  Remove-Item Env:PF_AUDIT_DATABASE_URL -ErrorAction SilentlyContinue
  Remove-Item Env:PF_PSQL -ErrorAction SilentlyContinue
  if ($started) { & (Join-Path $PostgresBin 'pg_ctl.exe') '-D' $clusterPath '-m' 'fast' '-w' 'stop' }
  # The disposable cluster and its log stay under ignored tmp/ for diagnostics.
}
