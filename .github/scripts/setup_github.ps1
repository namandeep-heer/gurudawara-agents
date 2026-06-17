# Sync secrets from .env to GitHub Actions (one-time / when credentials change).
# Requires: GitHub CLI — install from https://cli.github.com/ then run: gh auth login

param(
    [string]$EnvFile = ".env",
    [string]$SecretsList = ".github/secrets.list",
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed. Get it from https://cli.github.com/"
}

gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Run 'gh auth login' first."
}

if (-not (Test-Path $EnvFile)) {
    Write-Error "Missing $EnvFile. Copy .env.example to .env and fill in your values."
}

if (-not (Test-Path $SecretsList)) {
    Write-Error "Missing $SecretsList"
}

# Parse .env into a hashtable
$Values = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $key = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1).Trim()
    if ($value.Length -ge 2 -and $value[0] -eq $value[-1] -and $value[0] -in '"', "'") {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $Values[$key] = $value
}

$RepoArg = @()
if ($Repo) { $RepoArg = @("--repo", $Repo) }

$Names = Get-Content $SecretsList |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }

$Set = 0
$Skipped = 0
foreach ($Name in $Names) {
    if (-not $Values.ContainsKey($Name) -or -not $Values[$Name]) {
        Write-Warning "Skipping $Name (empty or missing in $EnvFile)"
        $Skipped++
        continue
    }
    $Values[$Name] | gh secret set $Name @RepoArg
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to set secret $Name"
    }
    Write-Host "Set secret: $Name"
    $Set++
}

Write-Host ""
Write-Host "Done. Set $Set secret(s), skipped $Skipped."
Write-Host "Shared config: hukamnama/config.env, scheduled_call/config.env, and service JSON/text files (committed to repo)."
Write-Host "No GitHub UI needed for variables - edit those files and push."