$projectRoot = Split-Path -Parent $PSScriptRoot
$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$launcherPath = Join-Path $startupDir 'agent-memory-bridge-service.cmd'
$legacyLauncherPath = Join-Path $startupDir 'cole-mem-bridge-service.cmd'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$servicePath = Join-Path $projectRoot 'scripts\run_mem_bridge_service.py'
$codexHome = Join-Path $HOME '.codex'
$bridgeHome = Join-Path $codexHome 'mem-bridge'
$configPath = Join-Path $bridgeHome 'config.toml'

$content = @"
@echo off
set "CODEX_HOME=$codexHome"
set "AGENT_MEMORY_BRIDGE_HOME=$bridgeHome"
set "AGENT_MEMORY_BRIDGE_CONFIG=$configPath"
start "" /min "$pythonPath" "$servicePath"
"@

Set-Content -LiteralPath $launcherPath -Value $content -Encoding ASCII
if (Test-Path -LiteralPath $legacyLauncherPath) {
    Remove-Item -LiteralPath $legacyLauncherPath -Force
    Write-Output "Removed legacy startup launcher: $legacyLauncherPath"
}
Write-Output "Installed startup launcher: $launcherPath"
