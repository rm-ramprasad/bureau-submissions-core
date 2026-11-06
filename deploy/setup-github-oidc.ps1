<#
.SYNOPSIS
  One-time setup so GitHub can deploy to rarfincare without stored secrets (step 9, issue #21). Run by the owner,
  once, after reviewing it: it CREATES Azure resources and role assignments (Claude never runs it).

.DESCRIPTION
  1. A user-assigned managed identity `bsub-github-deploy` in the resource group (no app registration needed).
  2. A federated credential on it that trusts GitHub's OIDC token for this repository's `main` branch only (a run
     from another branch, a fork or another repository cannot use it). GitHub Free has no deployment environments
     for private repositories, so the trust is on the branch.
  3. Its roles, as narrow as the deploy allows:
       Website Contributor on the Function App   (publish code, read the function key for the migrate job)
       Contributor on the Static Web App         (read the deployment token, publish pages)
       Reader on the resource group              (look the resources up)
     No access to the database, the storage account data or anything else.
  4. The repository's Actions variables (not secrets: these values are identifiers, not credentials). Needs the GitHub
     CLI signed in (`gh auth login`) as a repository admin.

.EXAMPLE
  .\deploy\setup-github-oidc.ps1 -Repo <owner>/<repository>
#>
param(
    [Parameter(Mandatory)][string]$Repo,                           # the repository that deploys, e.g. company/bureau-submissions-core
    [string]$Subscription = "RarFinance",
    [string]$ResourceGroup = "rg-bureau-submissions",
    [string]$FunctionApp = "bsub-func-73vcgqid4kh3c",
    [string]$StaticWebApp = "bsub-web-73vcgqid4kh3c",
    [string]$EntraTenantId = "4592988e-36ec-47c1-9467-1bebc76d59b2",
    [string]$EntraClientId = "fd66a8e5-485b-4dbe-818c-12221b749a2d",
    [string]$Identity = "bsub-github-deploy"
)
$ErrorActionPreference = "Stop"
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

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
$fa = az functionapp show -g $ResourceGroup -n $FunctionApp --query id -o tsv; Check "az functionapp show"
$swa = az staticwebapp show -g $ResourceGroup -n $StaticWebApp --query id -o tsv; Check "az staticwebapp show"
foreach ($r in @(@("Website Contributor", $fa), @("Contributor", $swa), @("Reader", $rg))) {
    az role assignment create --assignee-object-id $id.principalId --assignee-principal-type ServicePrincipal `
        --role $r[0] --scope $r[1] | Out-Null
    Check "role $($r[0])"
}

Write-Host "== GitHub Actions variables on $Repo" -ForegroundColor Cyan
$vars = [ordered]@{
    AZURE_CLIENT_ID = $id.clientId; AZURE_TENANT_ID = $tenantId; AZURE_SUBSCRIPTION_ID = $subId
    RESOURCE_GROUP = $ResourceGroup; FUNCTION_APP = $FunctionApp; STATIC_WEB_APP = $StaticWebApp
    ENTRA_TENANT_ID = $EntraTenantId; ENTRA_CLIENT_ID = $EntraClientId
}
foreach ($k in $vars.Keys) { gh variable set $k --repo $Repo --body $vars[$k]; Check "gh variable $k" }

Write-Host "`nDone. Role assignments can take a few minutes to apply. Then: push a tag (v1.0.0) to build a release, and"
Write-Host "run Actions > Deploy with that version." -ForegroundColor Green
