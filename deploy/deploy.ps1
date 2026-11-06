<#
.SYNOPSIS
  Build and deploy Bureau Submissions (this repository) to rarfincare's existing Azure resources: the same Function
  App, Static Web App and app settings as the previous app (resource group rg-bureau-submissions).

.DESCRIPTION
  Function App: builds a Linux / Python 3.14 package on this machine (the workspace packages as wheels, the
  third-party packages as manylinux wheels; no remote build), then publishes it with Azure Functions Core Tools.
  Pages: copies frontend/, writes config.js (Entra sign-in + API address) and the API address into the CSP, then
  deploys with the Static Web Apps CLI (the deployment token is fetched at run time and kept in memory only).
  Needs: uv, az (signed in to the RarFinance subscription), func (Core Tools v4), node/npx. Run from the repo root.
  Creates or changes no Azure resource: it only publishes code to the existing Function App and Static Web App.

.EXAMPLE
  .\deploy\deploy.ps1 -FunctionOnly    # code changed (the screens are unchanged by the move)
  .\deploy\deploy.ps1 -WebOnly         # frontend/ changed
  .\deploy\deploy.ps1                  # both
#>
param(
    [switch]$FunctionOnly,
    [switch]$WebOnly,
    [string]$Subscription = "RarFinance",
    [string]$ResourceGroup = "rg-bureau-submissions",
    [string]$FunctionApp = "bsub-func-73vcgqid4kh3c",
    [string]$StaticWebApp = "bsub-web-73vcgqid4kh3c",
    [string]$EntraTenantId = "4592988e-36ec-47c1-9467-1bebc76d59b2",
    [string]$EntraClientId = "fd66a8e5-485b-4dbe-818c-12221b749a2d"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Root "dist"
$SwaCli = "@azure/static-web-apps-cli@2.0.2"
$ApiBase = "https://$FunctionApp.azurewebsites.net"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$Func = (Get-Command func -ErrorAction SilentlyContinue).Source
if (-not $Func) { $Func = "$env:ProgramFiles\Microsoft\Azure Functions Core Tools\func.exe" }
foreach ($tool in @{ uv = (Get-Command uv -ErrorAction SilentlyContinue); az = (Get-Command az -ErrorAction SilentlyContinue); func = (Test-Path $Func); npx = (Get-Command npx -ErrorAction SilentlyContinue) }.GetEnumerator()) {
    if (-not $tool.Value) { throw "$($tool.Key) not found: install it and open a new terminal" }
}

az account set --subscription $Subscription; Check "az account set"

if (-not $WebOnly) {
    Step "Function App package (Linux, Python 3.14)"
    $pkg = Join-Path $Dist "function"
    if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
    New-Item -ItemType Directory -Force $pkg | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "function_app.py") $pkg
    Copy-Item (Join-Path $PSScriptRoot "host.json") $pkg
    $site = Join-Path $pkg ".python_packages\lib\site-packages"
    # third-party packages: exactly the locked versions, as Linux wheels
    $req = Join-Path $Dist "requirements-linux.txt"
    uv export --locked --no-dev --package shell-azure --no-emit-workspace --no-hashes --format requirements-txt -o $req
    Check "uv export"
    uv pip install --quiet --target $site --python-platform x86_64-manylinux_2_28 --python-version 3.14 --only-binary :all: -r $req
    Check "uv pip install (Linux wheels)"
    # the workspace packages (bureau_core, canonical, sources, clientconfig, app, shell_azure): pure Python wheels
    $wheels = Join-Path $Dist "wheels"
    uv build --all-packages --wheel --out-dir $wheels; Check "uv build"
    uv pip install --quiet --target $site --no-deps --python-platform x86_64-manylinux_2_28 --python-version 3.14 (Get-ChildItem $wheels -Filter *.whl).FullName
    Check "uv pip install (workspace wheels)"
    foreach ($secret in ".env.sql", ".env", "local.settings.json") {
        if (Get-ChildItem $pkg -Recurse -Force -Filter $secret) { throw "$secret must never be packaged" }
    }

    Step "Publish to $FunctionApp"
    Push-Location $pkg
    try { & $Func azure functionapp publish $FunctionApp --python --no-build; Check "func publish" }
    finally { Pop-Location }
}

if (-not $FunctionOnly) {
    Step "Pages"
    $web = Join-Path $Dist "web"
    if (Test-Path $web) { Remove-Item -Recurse -Force $web }
    Copy-Item -Recurse (Join-Path $Root "frontend") $web
    @"
// Written by deploy/deploy.ps1. Sign-in with Microsoft Entra ID; the API is the Function App (another origin, CORS).
window.APP_CONFIG = { auth: "entra", tenantId: "$EntraTenantId", clientId: "$EntraClientId",
                      apiScope: "api://$EntraClientId/access_as_user", apiBase: "$ApiBase" };
"@ | Set-Content -Encoding utf8 (Join-Path $web "config.js")
    $swaConfig = Join-Path $web "staticwebapp.config.json"
    (Get-Content -Raw $swaConfig).Replace("__API_BASE__", $ApiBase) | Set-Content -Encoding utf8 -NoNewline $swaConfig

    Step "Deploy pages to $StaticWebApp"
    $token = az staticwebapp secrets list -n $StaticWebApp -g $ResourceGroup --query "properties.apiKey" -o tsv
    Check "az staticwebapp secrets list"
    try { npx --yes $SwaCli deploy $web --deployment-token $token --env production; Check "swa deploy" }
    finally { $token = $null }
}

Step "Done"
if (-not $WebOnly) { Write-Host "API:   $ApiBase/api/meta (401 without sign-in is expected: Easy Auth)" }
