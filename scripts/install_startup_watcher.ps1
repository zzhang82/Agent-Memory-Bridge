$projectRoot = Split-Path -Parent $PSScriptRoot
$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$launcherPath = Join-Path $startupDir 'agent-memory-bridge-service.cmd'
$pythonPath = Join-Path $projectRoot '.venv311\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
$codexHome = Join-Path $HOME '.codex'
$bridgeHome = Join-Path $codexHome 'mem-bridge'
$configPath = Join-Path $bridgeHome 'config.toml'
$logDir = Join-Path $bridgeHome 'logs'
$stdoutLog = Join-Path $logDir 'startup-service.stdout.log'
$stderrLog = Join-Path $logDir 'startup-service.stderr.log'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python runtime not found. Expected .venv311 or .venv under $projectRoot"
}

$content = @"
@echo off
set "CODEX_HOME=$codexHome"
set "AGENT_MEMORY_BRIDGE_HOME=$bridgeHome"
set "AGENT_MEMORY_BRIDGE_CONFIG=$configPath"
if not exist "$logDir" mkdir "$logDir"
start "Agent Memory Bridge Service" /min "$pythonPath" -m agent_mem_bridge service 1>>"$stdoutLog" 2>>"$stderrLog"
"@

Set-Content -LiteralPath $launcherPath -Value $content -Encoding ASCII
Write-Output "Installed startup launcher: $launcherPath"
