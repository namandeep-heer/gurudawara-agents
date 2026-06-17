# Manually trigger the Daily Hukamnama GitHub Actions workflow.
# Requires: gh auth login

param(
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed. Get it from https://cli.github.com/"
}

gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Run 'gh auth login' first."
}

$RepoArg = @()
if ($Repo) {
    $RepoArg = @("--repo", $Repo)
} elseif (Test-Path ".git/config") {
    $Origin = git config --get remote.origin.url 2>$null
    if ($Origin -match 'github\.com[:/](.+?)(?:\.git)?$') {
        $RepoArg = @("--repo", $Matches[1])
    }
}

gh workflow run daily-hukamnama.yml @RepoArg
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to trigger workflow"
}

Write-Host "Triggered Daily Hukamnama workflow."
Start-Sleep -Seconds 3
gh run list --workflow=daily-hukamnama.yml --limit 1 @RepoArg