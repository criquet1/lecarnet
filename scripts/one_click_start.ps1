param(
    [string]$ConfigPath = "scripts/oneclick.config.json",
    [switch]$NoRunServer
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ConfigPath)) {
    throw "Config introuvable: $ConfigPath"
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

if (-not (Test-Path "venv/Scripts/python.exe")) {
    throw "Python introuvable dans venv/Scripts/python.exe"
}

$python = "venv/Scripts/python.exe"

$env:DEFAULT_DB_ENGINE = [string]$config.defaultDb.engine
$env:DEFAULT_DB_NAME = [string]$config.defaultDb.name
$env:DEFAULT_DB_USER = [string]$config.defaultDb.user
$env:DEFAULT_DB_PASSWORD = [string]$config.defaultDb.password
$env:DEFAULT_DB_HOST = [string]$config.defaultDb.host
$env:DEFAULT_DB_PORT = [string]$config.defaultDb.port

$tenantJson = $config.tenants | ConvertTo-Json -Compress -Depth 10
$env:TENANT_DATABASES_JSON = $tenantJson

$env:ONECLICK_ADMIN_USERNAME = [string]$config.adminUser.username
$env:ONECLICK_ADMIN_EMAIL = [string]$config.adminUser.email
$env:ONECLICK_ADMIN_PASSWORD = [string]$config.adminUser.password

Write-Host "Configuration chargee depuis $ConfigPath" -ForegroundColor Cyan
Write-Host "Bootstrap multiclient en cours..." -ForegroundColor Cyan

& $python manage.py oneclick_bootstrap
if ($LASTEXITCODE -ne 0) {
    throw "Echec du bootstrap oneclick"
}

& $python manage.py check
if ($LASTEXITCODE -ne 0) {
    throw "Le check Django a echoue"
}

if (-not $NoRunServer) {
    Write-Host "Serveur sur http://127.0.0.1:8000" -ForegroundColor Green
    & $python manage.py runserver
}
