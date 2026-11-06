<#
.SYNOPSIS
  One-time setup per client so their deployment repository can deploy to their Azure without stored secrets (step 9,
  issue #21). Run by the owner, once per client, after reviewing it: it CREATES Azure resources and role assignments
  (Claude never runs it).

.DESCRIPTION
  Reads the client's install.json (installs/<client>/install.json: their deployment repository and Azure names), then:
  1. A user-assigned managed identity `bsub-github-deploy` in the client's resource group (no app registration).
  2. A federated credential on it that trusts GitHub's OIDC token from the deployment repository's `main` branch only
     (another branch, a fork or another repository cannot use it; GitHub Free has no deployment environments for
     private repositories, so the trust is on the branch).
  3. Its roles, as narrow as the deploy allows:
       Website Contributor on the Function App   (publish code, read the function key for the migrate job)
       Contributor on the Static Web App         (read the deployment token, publish pages)
       Reader on the resource group              (look the resources up)
     No access to the database, the storage account data or anything else.
  4. The deployment repository's Actions variables AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
     (identifiers, not secrets). Needs the GitHub CLI signed in (`gh auth login`) with admin rights on that repository.

.EXAMPLE
  .\deploy\setup-github-oidc.ps1 -Client rarfincare -Subscription RarFinance
#>
param(
    [Parameter(Mandatory)][string]$Client,                         # installs/<client>/install.json
    [Parameter(Mandatory)][string]$Subscription,                   # the client's Azure subscription (name or id)
    [string]$Identity = "bsub-github-deploy"
)
$ErrorActionPreference = "Stop"
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$install = Get-Content -Raw (Join-Path (Split-Path -Parent $PSScriptRoot) "installs\$Client\install.json") | ConvertFrom-Json
$Repo, $ResourceGroup = $install.repo, $install.resource_group
Write-Host "Client $Client`: repository $Repo, resource group $ResourceGroup" -ForegroundColor Cyan

az account set --subscription $Subscription; Check "az account set"
$subId = az account show --query id -o tsv; Check "az account show"
$tenantId = az account show --query tenantId -o tsv

Write-Host "== Managed identity $Identity" -ForegroundColor Cyan
$id = az identity create -g $ResourceGroup -n $Identity --query "{clientId:clientId, principalId:principalId}" -o json | ConvertFrom-Json
Check "az identity create"

Write-Host "== Federated credential: $Repo, branch main" -ForegroundColor Cyan
az identity federated-credential create -g $ResourceGroup --identity-name $Identity -n "github-main" `
    --issuer "https://token.actions.githubusercontent.com" --subject "repo:${Repo}:ref:refs/heads/main" `
    --audiences "api://AzureADTokenExchange" | Out-Null
Check "az identity federated-credential create"

Write-Host "== Roles" -ForegroundColor Cyan
$rg = az group show -n $ResourceGroup --query id -o tsv; Check "az group show"
$fa = az functionapp show -g $ResourceGroup -n $install.function_app --query id -o tsv; Check "az functionapp show"
$swa = az staticwebapp show -g $ResourceGroup -n $install.static_web_app --query id -o tsv; Check "az staticwebapp show"
foreach ($r in @(@("Website Contributor", $fa), @("Contributor", $swa), @("Reader", $rg))) {
    az role assignment create --assignee-object-id $id.principalId --assignee-principal-type ServicePrincipal `
        --role $r[0] --scope $r[1] | Out-Null
    Check "role $($r[0])"
}

Write-Host "== GitHub Actions variables on $Repo" -ForegroundColor Cyan
$vars = [ordered]@{ AZURE_CLIENT_ID = $id.clientId; AZURE_TENANT_ID = $tenantId; AZURE_SUBSCRIPTION_ID = $subId }
foreach ($k in $vars.Keys) { gh variable set $k --repo $Repo --body $vars[$k]; Check "gh variable $k" }

Write-Host "`nDone. Role assignments can take a few minutes to apply." -ForegroundColor Green
