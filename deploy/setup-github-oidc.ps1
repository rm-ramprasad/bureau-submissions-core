<#
.SYNOPSIS
  One-time setup per client so their deployment repository can deploy to their Azure without stored secrets (step 9,
  issue #21). Run by the owner, once per client, after reviewing it: it CREATES Azure resources and role assignments
  (Claude never runs it).

.DESCRIPTION
  Reads the client's install.json (installs/<client>/install.json: their deployment repository and Azure names), then:
  1. A user-assigned managed identity `bsub-github-deploy` in the client's resource group (no app registration).
  2. A federated credential on it that trusts GitHub's OIDC token from the deployment repository's `main` branch only.
  3. Its roles, as narrow as the deploy allows:
       Website Contributor on the Function App   (publish code, read the function key for the migrate job)
       Contributor on the Static Web App         (read the deployment token, publish pages)
       Reader on the resource group              (look the resources up)
     No access to the database, the storage account data or anything else.
  4. The deployment repository's Actions variables AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID.

  -Preview: only reads (who you are signed in as, the resource IDs, the existing role assignments) and prints every
  change it would make, with the exact command. Nothing is created or changed.

  Every run (preview or not) writes a log to deploy\logs\setup-<client>-<time>.log: the signed-in Azure and GitHub
  accounts, the resource group's role assignments before and after, each command and its result. Keep it for audit.
  Azure also records each change in the resource group's Activity log (who, when, what).

.EXAMPLE
  .\deploy\setup-github-oidc.ps1 -Client rarfincare -Subscription RarFinance -Preview   # look first
  .\deploy\setup-github-oidc.ps1 -Client rarfincare -Subscription RarFinance            # then do it
#>
param(
    [Parameter(Mandatory)][string]$Client,                         # installs/<client>/install.json
    [Parameter(Mandatory)][string]$Subscription,                   # the client's Azure subscription (name or id)
    [string]$Identity = "bsub-github-deploy",
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("setup-{0}-{1}{2}.log" -f $Client, (Get-Date -Format "yyyyMMdd-HHmmss"), $(if ($Preview) { "-preview" } else { "" }))
Start-Transcript -Path $log | Out-Null

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function Change($what, [scriptblock]$command) {
    # A change: printed with its command; run unless -Preview.
    Write-Host "`n-- $what" -ForegroundColor Yellow
    Write-Host ("   " + $ExecutionContext.InvokeCommand.ExpandString($command.ToString().Trim()))
    if ($Preview) { Write-Host "   (preview: not run)" -ForegroundColor DarkGray; return $null }
    $result = & $command
    Check $what
    return $result
}

try {
    $install = Get-Content -Raw (Join-Path $Root "installs\$Client\install.json") | ConvertFrom-Json
    $Repo, $ResourceGroup = $install.repo, $install.resource_group
    Write-Host ("== {0}: client {1}, repository {2}, resource group {3}{4}" -f (Get-Date -Format s), $Client, $Repo,
        $ResourceGroup, $(if ($Preview) { " (PREVIEW: nothing will be changed)" } else { "" })) -ForegroundColor Cyan

    az account set --subscription $Subscription; Check "az account set"
    $user = az account show --query user.name -o tsv; Check "az account show"
    $subName = az account show --query name -o tsv
    $subId = az account show --query id -o tsv
    $tenantId = az account show --query tenantId -o tsv
    Write-Host "Azure: signed in as $user, subscription $subName ($subId)"
    Write-Host "GitHub: $(gh api user --jq .login 2>$null)  (needs admin on $Repo)"

    $rg = az group show -n $ResourceGroup --query id -o tsv; Check "az group show"
    $fa = az functionapp show -g $ResourceGroup -n $install.function_app --query id -o tsv; Check "az functionapp show"
    $swa = az staticwebapp show -g $ResourceGroup -n $install.static_web_app --query id -o tsv; Check "az staticwebapp show"
    Write-Host "`n== Role assignments in $ResourceGroup BEFORE" -ForegroundColor Cyan
    az role assignment list -g $ResourceGroup --include-inherited false -o table

    $existing = az identity show -g $ResourceGroup -n $Identity --query principalId -o tsv 2>$null
    if ($existing) { Write-Host "`nIdentity $Identity exists already: it is reused." }

    Change "Create managed identity $Identity in $ResourceGroup" {
        az identity create -g $ResourceGroup -n $Identity -o none
    } | Out-Null
    if ($Preview -and -not $existing) {
        $principal, $clientId = "<new identity's principal id>", "<new identity's client id>"
    } else {
        $principal = az identity show -g $ResourceGroup -n $Identity --query principalId -o tsv; Check "az identity show"
        $clientId = az identity show -g $ResourceGroup -n $Identity --query clientId -o tsv
    }

    Change "Federated credential github-main: trust GitHub runs from $Repo, branch main only" {
        az identity federated-credential create -g $ResourceGroup --identity-name $Identity -n github-main --issuer https://token.actions.githubusercontent.com --subject "repo:${Repo}:ref:refs/heads/main" --audiences api://AzureADTokenExchange -o none
    } | Out-Null
    Change "Role Website Contributor on the Function App $($install.function_app)" {
        az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Website Contributor" --scope $fa -o none
    } | Out-Null
    Change "Role Contributor on the Static Web App $($install.static_web_app)" {
        az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role Contributor --scope $swa -o none
    } | Out-Null
    Change "Role Reader on the resource group $ResourceGroup" {
        az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role Reader --scope $rg -o none
    } | Out-Null
    foreach ($pair in @(@("AZURE_CLIENT_ID", $clientId), @("AZURE_TENANT_ID", $tenantId), @("AZURE_SUBSCRIPTION_ID", $subId))) {
        $name, $value = $pair
        Change "GitHub variable $name on $Repo" { gh variable set $name --repo $Repo --body $value } | Out-Null
    }

    if (-not $Preview) {
        Write-Host "`n== Role assignments in $ResourceGroup AFTER" -ForegroundColor Cyan
        az role assignment list -g $ResourceGroup --include-inherited false -o table
        Write-Host "`nDone. Role assignments can take a few minutes to apply." -ForegroundColor Green
    } else {
        Write-Host "`nPreview only: nothing was created or changed. Run again without -Preview to do it." -ForegroundColor Green
    }
}
finally {
    Stop-Transcript | Out-Null
    Write-Host "Log: $log"
}
