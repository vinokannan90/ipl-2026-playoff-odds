<#
.SYNOPSIS
    One-shot bootstrap for IPL 2026 Playoff Odds: OIDC app registration,
    federated credentials, RBAC, Key Vault secret, and GitHub Actions wiring.

.DESCRIPTION
    Idempotent. Safe to re-run. Performs the following steps:

      1. Verifies az CLI + GitHub CLI are installed and logged in
      2. Creates (or reuses) the resource group
      3. Creates (or reuses) an Entra app registration + service principal
      4. Adds federated credentials for:
           - main branch deployments
           - pull-request validation
           - GitHub Environments (optional)
      5. Grants Contributor on the resource group to the SP
      6. (After azd up) Stores the GitHub Models token in Key Vault
      7. Sets GitHub repo Variables and Secrets via gh CLI

    The script is split into "phases". Use -Phase to run a single phase, or
    omit it to run everything end-to-end (the default).

.PARAMETER SubscriptionId
    Azure subscription ID. Default: dc958fdf-4614-4532-b9d0-9627780aad94

.PARAMETER ResourceGroup
    Target resource group. Default: rg-iplodds-prod

.PARAMETER Location
    Azure region. Default: centralindia

.PARAMETER NamePrefix
    Resource name prefix used by the Bicep template. Default: iplodds

.PARAMETER GithubRepo
    GitHub repo in <owner>/<repo> form. Auto-detected from `git remote` if omitted.

.PARAMETER AppName
    Entra app registration display name. Default: iplodds-gha

.PARAMETER GithubModelsToken
    GitHub Models token to write to Key Vault. If omitted, you'll be prompted
    securely (or skipped with -SkipKeyVaultSecret).

.PARAMETER Phase
    Run a single phase instead of all. One of:
      preflight, oidc, rbac, secret, ghvars, all

.PARAMETER SkipKeyVaultSecret
    Skip writing the github-models-token to Key Vault.

.PARAMETER SkipGithubWiring
    Skip setting GitHub Actions Variables/Secrets via gh CLI.

.PARAMETER WhatIf
    Print actions without executing.

.EXAMPLE
    pwsh ./scripts/bootstrap.ps1
    # Full end-to-end bootstrap, prompts for the Models token

.EXAMPLE
    pwsh ./scripts/bootstrap.ps1 -Phase oidc
    # Just (re)create the OIDC app registration + federated credentials

.EXAMPLE
    pwsh ./scripts/bootstrap.ps1 -Phase secret -GithubModelsToken (Read-Host -AsSecureString)
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $SubscriptionId = 'dc958fdf-4614-4532-b9d0-9627780aad94',
    [string] $ResourceGroup  = 'rg-iplodds-prod',
    [string] $Location       = 'centralindia',
    [string] $NamePrefix     = 'iplodds',
    [string] $GithubRepo,
    [string] $AppName        = 'iplodds-gha',
    [SecureString] $GithubModelsToken,
    [ValidateSet('preflight', 'oidc', 'rbac', 'secret', 'ghvars', 'all')]
    [string] $Phase = 'all',
    [switch] $SkipKeyVaultSecret,
    [switch] $SkipGithubWiring
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------- Helpers ----------

function Write-Step {
    param([string] $Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string] $Message)
    Write-Host "    OK  $Message" -ForegroundColor Green
}

function Write-Skip {
    param([string] $Message)
    Write-Host "    --  $Message" -ForegroundColor DarkGray
}

function Assert-Command {
    param([string] $Name, [string] $InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' not found on PATH. Install: $InstallHint"
    }
}

function Invoke-Az {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments = $true)][string[]] $Args)
    if ($PSCmdlet.ShouldProcess("az $($Args -join ' ')", 'execute')) {
        $output = & az @Args 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "az $($Args -join ' ') failed:`n$output"
        }
        return $output
    }
}

function Get-RepoFromGitRemote {
    try {
        $url = git config --get remote.origin.url 2>$null
        if (-not $url) { return $null }
        # Handles git@github.com:owner/repo(.git) and https://github.com/owner/repo(.git)
        if ($url -match 'github\.com[:/]([^/]+)/([^/.]+)(\.git)?$') {
            return "$($Matches[1])/$($Matches[2])"
        }
    } catch {
        return $null
    }
    return $null
}

# ---------- Phase: preflight ----------

function Invoke-Preflight {
    Write-Step 'Preflight checks'

    Assert-Command az 'https://learn.microsoft.com/cli/azure/install-azure-cli'
    Write-Ok 'az CLI present'

    if (-not $SkipGithubWiring) {
        Assert-Command gh 'https://cli.github.com/'
        Write-Ok 'gh CLI present'
    }

    # Verify az login
    $account = az account show --output json 2>$null | ConvertFrom-Json
    if (-not $account) {
        throw "Not logged in to Azure. Run: az login"
    }
    Write-Ok "az logged in as $($account.user.name)"

    # Set subscription
    Invoke-Az account set --subscription $SubscriptionId | Out-Null
    Write-Ok "Subscription set: $SubscriptionId"

    # Verify gh login (only if needed)
    if (-not $SkipGithubWiring) {
        $ghStatus = gh auth status 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            throw "Not logged in to GitHub. Run: gh auth login"
        }
        Write-Ok 'gh CLI authenticated'
    }

    # Auto-detect repo
    if (-not $GithubRepo) {
        $script:GithubRepo = Get-RepoFromGitRemote
        if (-not $script:GithubRepo) {
            throw 'Could not auto-detect GitHub repo. Pass -GithubRepo <owner>/<repo>.'
        }
        Write-Ok "Detected repo: $script:GithubRepo"
    }

    # Ensure resource group exists (created here so RBAC scope is valid)
    $rg = az group show --name $ResourceGroup --output json 2>$null | ConvertFrom-Json
    if (-not $rg) {
        Write-Step "Creating resource group $ResourceGroup in $Location"
        Invoke-Az group create --name $ResourceGroup --location $Location | Out-Null
    }
    Write-Ok "Resource group: $ResourceGroup"
}

# ---------- Phase: oidc ----------

function Invoke-Oidc {
    Write-Step "Ensuring Entra app registration '$AppName'"

    # Find or create the app
    $appId = az ad app list --display-name $AppName --query '[0].appId' --output tsv 2>$null
    if (-not $appId) {
        $appId = Invoke-Az ad app create --display-name $AppName --query appId --output tsv
        Write-Ok "Created app registration: $appId"
    } else {
        Write-Ok "Reusing app registration: $appId"
    }

    # Ensure SP exists
    $spId = az ad sp list --filter "appId eq '$appId'" --query '[0].id' --output tsv 2>$null
    if (-not $spId) {
        Invoke-Az ad sp create --id $appId | Out-Null
        Write-Ok 'Created service principal'
    } else {
        Write-Ok 'Service principal exists'
    }

    # Federated credentials we want
    $repo = $GithubRepo
    $desiredCreds = @(
        @{
            name      = 'gh-main'
            subject   = "repo:${repo}:ref:refs/heads/main"
            desc      = 'Deploys from main branch'
        },
        @{
            name      = 'gh-pull-request'
            subject   = "repo:${repo}:pull_request"
            desc      = 'PR validation runs'
        },
        @{
            name      = 'gh-env-prod'
            subject   = "repo:${repo}:environment:prod"
            desc      = 'Optional: production GitHub Environment'
        }
    )

    $existing = az ad app federated-credential list --id $appId --output json | ConvertFrom-Json
    $existingNames = @($existing | ForEach-Object { $_.name })

    foreach ($cred in $desiredCreds) {
        if ($existingNames -contains $cred.name) {
            Write-Skip "Federated credential '$($cred.name)' already present"
            continue
        }
        $tmp = New-TemporaryFile
        try {
            $payload = @{
                name        = $cred.name
                issuer      = 'https://token.actions.githubusercontent.com'
                subject     = $cred.subject
                description = $cred.desc
                audiences   = @('api://AzureADTokenExchange')
            } | ConvertTo-Json -Depth 5
            Set-Content -Path $tmp -Value $payload -Encoding utf8
            Invoke-Az ad app federated-credential create --id $appId --parameters "@$tmp" | Out-Null
            Write-Ok "Added federated credential: $($cred.name) -> $($cred.subject)"
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
    }

    $script:AppId    = $appId
    $script:TenantId = (az account show --query tenantId --output tsv)
    Write-Ok "AppId   = $script:AppId"
    Write-Ok "TenantId = $script:TenantId"
}

# ---------- Phase: rbac ----------

function Invoke-Rbac {
    if (-not $script:AppId) {
        $script:AppId = az ad app list --display-name $AppName --query '[0].appId' --output tsv
        if (-not $script:AppId) { throw "App '$AppName' not found. Run -Phase oidc first." }
    }

    Write-Step "Granting Contributor on $ResourceGroup to SP"
    $scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup"

    $existing = az role assignment list --assignee $script:AppId --scope $scope `
        --role Contributor --query '[0].id' --output tsv 2>$null
    if ($existing) {
        Write-Skip 'Contributor role already assigned'
    } else {
        Invoke-Az role assignment create --assignee $script:AppId --role Contributor --scope $scope | Out-Null
        Write-Ok 'Contributor granted'
    }

    # Also need User Access Administrator-equivalent? No -- Bicep does its own RBAC via system principal,
    # but the SP needs to assign roles during deployment. Add 'Role Based Access Control Administrator'
    # scoped tightly to the RG so Bicep can create role assignments for the managed identity.
    $rbacAdminRole = 'Role Based Access Control Administrator'
    $existingRbac = az role assignment list --assignee $script:AppId --scope $scope `
        --role $rbacAdminRole --query '[0].id' --output tsv 2>$null
    if ($existingRbac) {
        Write-Skip "$rbacAdminRole already assigned"
    } else {
        Invoke-Az role assignment create --assignee $script:AppId --role $rbacAdminRole --scope $scope | Out-Null
        Write-Ok "$rbacAdminRole granted (needed for Bicep role assignments)"
    }
}

# ---------- Phase: secret ----------

function Invoke-Secret {
    if ($SkipKeyVaultSecret) {
        Write-Skip 'Key Vault secret skipped (-SkipKeyVaultSecret)'
        return
    }

    Write-Step 'Storing GitHub Models token in Key Vault'

    # Resolve the Key Vault name created by Bicep
    $kvName = az keyvault list --resource-group $ResourceGroup `
        --query "[?starts_with(name, '${NamePrefix}-kv-')].name | [0]" --output tsv 2>$null
    if (-not $kvName) {
        Write-Host '    !! Key Vault not found yet. Run `azd up` first, then re-run with -Phase secret.' -ForegroundColor Yellow
        return
    }
    Write-Ok "Key Vault: $kvName"

    if (-not $GithubModelsToken) {
        $GithubModelsToken = Read-Host -AsSecureString -Prompt 'Paste GitHub Models token (input hidden)'
    }
    if ($GithubModelsToken.Length -eq 0) {
        Write-Host '    !! Empty token, skipping' -ForegroundColor Yellow
        return
    }

    # Grant current user Key Vault Secrets Officer so we can write the secret
    $me = (az ad signed-in-user show --query id --output tsv)
    $kvScope = (az keyvault show --name $kvName --query id --output tsv)
    $hasRole = az role assignment list --assignee $me --scope $kvScope `
        --role 'Key Vault Secrets Officer' --query '[0].id' --output tsv 2>$null
    if (-not $hasRole) {
        Invoke-Az role assignment create --assignee $me --role 'Key Vault Secrets Officer' --scope $kvScope | Out-Null
        Write-Ok 'Granted self Key Vault Secrets Officer'
        Start-Sleep -Seconds 10  # RBAC propagation
    }

    # Convert SecureString -> plain only at the boundary of the az call
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($GithubModelsToken)
    try {
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        Invoke-Az keyvault secret set --vault-name $kvName --name 'github-models-token' --value $plain | Out-Null
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $plain = $null
    }
    Write-Ok 'Secret github-models-token written'
}

# ---------- Phase: ghvars ----------

function Invoke-GhVars {
    if ($SkipGithubWiring) {
        Write-Skip 'GitHub wiring skipped (-SkipGithubWiring)'
        return
    }

    Write-Step "Setting GitHub Actions variables on $GithubRepo"

    if (-not $script:AppId)    { $script:AppId    = az ad app list --display-name $AppName --query '[0].appId' --output tsv }
    if (-not $script:TenantId) { $script:TenantId = az account show --query tenantId --output tsv }

    # Discover deployed resource names (best-effort; may be empty before azd up)
    $caName  = az containerapp list --resource-group $ResourceGroup --query '[0].name' --output tsv 2>$null
    $caEnv   = az containerapp env list --resource-group $ResourceGroup --query '[0].name' --output tsv 2>$null
    $backend = $null
    if ($caName) {
        $fqdn = az containerapp show --name $caName --resource-group $ResourceGroup `
            --query 'properties.configuration.ingress.fqdn' --output tsv 2>$null
        if ($fqdn) { $backend = "https://$fqdn" }
    }

    $vars = [ordered]@{
        AZURE_CLIENT_ID       = $script:AppId
        AZURE_TENANT_ID       = $script:TenantId
        AZURE_SUBSCRIPTION_ID = $SubscriptionId
        AZURE_RG              = $ResourceGroup
        AZURE_LOCATION        = $Location
        AZURE_NAME_PREFIX     = $NamePrefix
    }
    if ($caName)  { $vars['AZURE_CONTAINER_APP'] = $caName }
    if ($caEnv)   { $vars['AZURE_CONTAINER_ENV'] = $caEnv }
    if ($backend) { $vars['BACKEND_URL']         = $backend }

    foreach ($entry in $vars.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace($entry.Value)) {
            Write-Skip "$($entry.Key) (empty -- run azd up then re-run -Phase ghvars)"
            continue
        }
        if ($PSCmdlet.ShouldProcess("$($entry.Key)=$($entry.Value)", 'gh variable set')) {
            gh variable set $entry.Key --repo $GithubRepo --body $entry.Value | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh variable set $($entry.Key) failed" }
            Write-Ok "var $($entry.Key)"
        }
    }

    # SWA deployment token (secret, not variable)
    $swaName = az staticwebapp list --resource-group $ResourceGroup --query '[0].name' --output tsv 2>$null
    if ($swaName) {
        $swaToken = az staticwebapp secrets list --name $swaName --resource-group $ResourceGroup `
            --query 'properties.apiKey' --output tsv 2>$null
        if ($swaToken) {
            if ($PSCmdlet.ShouldProcess('AZURE_STATIC_WEB_APPS_API_TOKEN', 'gh secret set')) {
                $swaToken | gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN --repo $GithubRepo | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN failed' }
                Write-Ok 'secret AZURE_STATIC_WEB_APPS_API_TOKEN'
            }
        }
    }
}

# ---------- Driver ----------

Write-Host "IPL 2026 Playoff Odds -- bootstrap" -ForegroundColor Magenta
Write-Host "  Subscription : $SubscriptionId"
Write-Host "  ResourceGroup: $ResourceGroup"
Write-Host "  Location     : $Location"
Write-Host "  Phase        : $Phase"

switch ($Phase) {
    'preflight' { Invoke-Preflight }
    'oidc'      { Invoke-Preflight; Invoke-Oidc }
    'rbac'      { Invoke-Preflight; Invoke-Rbac }
    'secret'    { Invoke-Preflight; Invoke-Secret }
    'ghvars'    { Invoke-Preflight; Invoke-GhVars }
    'all' {
        Invoke-Preflight
        Invoke-Oidc
        Invoke-Rbac
        Invoke-Secret
        Invoke-GhVars
    }
}

Write-Host ''
Write-Host "Done." -ForegroundColor Magenta
Write-Host "Next:" -ForegroundColor Magenta
Write-Host "  1. azd up                        # provision + deploy"
Write-Host "  2. ./scripts/bootstrap.ps1 -Phase secret   # if you skipped before azd up"
Write-Host "  3. ./scripts/bootstrap.ps1 -Phase ghvars   # populate GH vars after azd up"
