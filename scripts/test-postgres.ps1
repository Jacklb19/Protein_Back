param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin', [int]$Port = 55439)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$clusterPath = Join-Path $repository ('tmp\pg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $clusterPath -Force | Out-Null
$databaseUrl = "postgresql://postgres@127.0.0.1:$Port/pf_security_test"
function Invoke-Pg([string]$Executable, [string[]]$Arguments) {
  & (Join-Path $PostgresBin $Executable) @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}
$started = $false
try {
  Invoke-Pg 'initdb.exe' @('-D', $clusterPath, '-U', 'postgres', '-A', 'trust', '--encoding=UTF8', '--locale=C')
  Invoke-Pg 'pg_ctl.exe' @('-D', $clusterPath, '-l', (Join-Path $clusterPath 'server.log'), '-o', "-h 127.0.0.1 -p $Port", '-w', 'start')
  $started = $true
  Invoke-Pg 'createdb.exe' @('-h', '127.0.0.1', '-p', "$Port", '-U', 'postgres', 'pf_security_test')
  Invoke-Pg 'psql.exe' @($databaseUrl, '-X', '-v', 'ON_ERROR_STOP=1', '-f', (Join-Path $repository 'tests\postgres_bootstrap.sql'))
  foreach ($migration in Get-ChildItem -LiteralPath (Join-Path $repository 'supabase\migrations') -Filter '*.sql' | Sort-Object Name) {
    Invoke-Pg 'psql.exe' @($databaseUrl, '-X', '-v', 'ON_ERROR_STOP=1', '-f', $migration.FullName)
  }
  $env:PF_TEST_DATABASE_URL = $databaseUrl
  $env:PF_PSQL = Join-Path $PostgresBin 'psql.exe'
  & (Join-Path $repository '.venv\Scripts\python.exe') -m unittest discover -s (Join-Path $repository 'tests') -p 'test_postgres.py' -v
  if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL security checks failed' }
} finally {
  Remove-Item Env:PF_TEST_DATABASE_URL -ErrorAction SilentlyContinue
  Remove-Item Env:PF_PSQL -ErrorAction SilentlyContinue
  if ($started) { & (Join-Path $PostgresBin 'pg_ctl.exe') '-D' $clusterPath '-m' 'fast' '-w' 'stop' }
  # Keep the isolated cluster and log under ignored tmp/ for diagnostics. Never remove an existing database.
}
