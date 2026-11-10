<#
.SYNOPSIS
  Run an admin job on the deployed Function App: POST /ops/<job> with the app's function key (fetched at run time,
  kept in memory only). Jobs: migrate | users | views | master-parity | recovery | retention.

.EXAMPLE
  .\deploy\ops.ps1 migrate
  .\deploy\ops.ps1 users add asha.k --email asha.k@rarfincare.com --name "Asha K" --admin
  .\deploy\ops.ps1 users list
  .\deploy\ops.ps1 retention -DryRun
  .\deploy\ops.ps1 views CONSUMER --as-of 2026-10-31 --load-id 17
  .\deploy\ops.ps1 master-parity CONSUMER --as-of 2026-10-31 --load-id 17
#>
[CmdletBinding(PositionalBinding = $false)]     # only the job is positional; everything after it goes to the job
param(
    [Parameter(Mandatory, Position = 0)][ValidateSet("migrate", "users", "views", "master-parity", "recovery", "retention")][string]$Job,
    [Parameter(ValueFromRemainingArguments)][string[]]$Args_,
    [switch]$DryRun,
    [string]$Subscription = "RarFinance",
    [string]$ResourceGroup = "rg-bureau-submissions",
    [string]$FunctionApp = "bsub-func-73vcgqid4kh3c"
)
$ErrorActionPreference = "Stop"
$key = az functionapp keys list --subscription $Subscription -g $ResourceGroup -n $FunctionApp --query "functionKeys.default" -o tsv
if (-not $key) { throw "Could not read the function key of $FunctionApp" }
$body = @{}
if ($Args_) { $body.args = $Args_ }
if ($DryRun) { $body.dry_run = $true }
try {
    $result = Invoke-RestMethod -Method Post -Uri "https://$FunctionApp.azurewebsites.net/ops/$Job" `
        -Headers @{ "x-functions-key" = $key } -ContentType "application/json" -Body ($body | ConvertTo-Json -Compress)
}
finally { $key = $null }
if ($result.output) { $result.output } else { $result | ConvertTo-Json -Depth 8 }
