# Apply hukamnama/trigger.env to .github/workflows/daily-hukamnama.yml
# Run from repo root: .github/scripts/configure_hukamnama_trigger.ps1

param(
    [string]$TriggerEnv = "hukamnama/trigger.env",
    [string]$WorkflowFile = ".github/workflows/daily-hukamnama.yml"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

function Read-EnvFile {
    param([string]$Path)
    $Values = @{}
    Get-Content $Path | ForEach-Object {
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
    return $Values
}

function Convert-IstToUtcCron {
    param([string]$IstTime)
    if ($IstTime -notmatch '^(\d{1,2}):(\d{2})$') {
        throw "Invalid IST time '$IstTime'. Use HH:MM (24h)."
    }
    $hour = [int]$Matches[1]
    $minute = [int]$Matches[2]
    if ($hour -gt 23 -or $minute -gt 59) {
        throw "Invalid IST time '$IstTime'. Hour must be 0-23, minute 0-59."
    }
    $istMinutes = $hour * 60 + $minute
    $utcMinutes = $istMinutes - 330
    if ($utcMinutes -lt 0) { $utcMinutes += 1440 }
    $utcHour = [math]::Floor($utcMinutes / 60)
    $utcMinute = $utcMinutes % 60
    return ("{0} {1} * * *" -f $utcMinute, $utcHour)
}

function Format-IstLabel {
    param([string]$IstTime)
    if ($IstTime -notmatch '^(\d{1,2}):(\d{2})$') { return $IstTime }
    $hour = [int]$Matches[1]
    $minute = $Matches[2]
    $suffix = if ($hour -ge 12) { "PM" } else { "AM" }
    $displayHour = $hour % 12
    if ($displayHour -eq 0) { $displayHour = 12 }
    return ("{0}:{1} {2}" -f $displayHour, $minute, $suffix)
}

function Format-UtcLabel {
    param([string]$CronExpr)
    if ($CronExpr -notmatch '^(\d+) (\d+) \* \* \*$') { return $CronExpr }
    return ("{0}:{1}" -f $Matches[2], $Matches[1].PadLeft(2, '0'))
}

if (-not (Test-Path $TriggerEnv)) {
    Write-Error "Missing $TriggerEnv"
}
if (-not (Test-Path $WorkflowFile)) {
    Write-Error "Missing $WorkflowFile"
}

$Config = Read-EnvFile $TriggerEnv
$Trigger = if ($Config.ContainsKey("HUKAMNAMA_TRIGGER")) { $Config["HUKAMNAMA_TRIGGER"].Trim().ToLower() } else { "cron_job_org" }
$PrimaryIst = if ($Config.ContainsKey("HUKAMNAMA_PRIMARY_IST")) { $Config["HUKAMNAMA_PRIMARY_IST"].Trim() } else { "09:00" }
$FallbackIst = if ($Config.ContainsKey("HUKAMNAMA_FALLBACK_IST")) { $Config["HUKAMNAMA_FALLBACK_IST"].Trim() } else { "09:10" }

if ($Trigger -notin @("cron_job_org", "github_schedule")) {
    Write-Error "HUKAMNAMA_TRIGGER must be 'cron_job_org' or 'github_schedule', got '$Trigger'"
}

$PrimaryCron = Convert-IstToUtcCron $PrimaryIst
$FallbackCron = Convert-IstToUtcCron $FallbackIst

$Lines = Get-Content $WorkflowFile
$BeginMarker = "# BEGIN_SCHEDULE"
$EndMarker = "# END_SCHEDULE"
$beginIdx = -1
$endIdx = -1
for ($i = 0; $i -lt $Lines.Count; $i++) {
    if ($Lines[$i] -match [regex]::Escape($BeginMarker)) { $beginIdx = $i }
    if ($Lines[$i] -match [regex]::Escape($EndMarker)) { $endIdx = $i; break }
}
if ($beginIdx -lt 0 -or $endIdx -lt 0 -or $endIdx -le $beginIdx) {
    Write-Error "Workflow missing $BeginMarker / $EndMarker markers."
}

$ScheduleLines = @("$($Lines[$beginIdx])")
if ($Trigger -eq "github_schedule") {
    $ScheduleLines += @(
        "  schedule:",
        "    # $(Format-IstLabel $PrimaryIst) IST ($(Format-UtcLabel $PrimaryCron) UTC) - primary",
        "    - cron: `"$PrimaryCron`"",
        "    # $(Format-IstLabel $FallbackIst) IST ($(Format-UtcLabel $FallbackCron) UTC) - fallback if the first run missed",
        "    - cron: `"$FallbackCron`""
    )
}
$ScheduleLines += $Lines[$endIdx]

$Updated = @()
if ($beginIdx -gt 0) { $Updated += $Lines[0..($beginIdx - 1)] }
$Updated += $ScheduleLines
if ($endIdx -lt ($Lines.Count - 1)) { $Updated += $Lines[($endIdx + 1)..($Lines.Count - 1)] }
Set-Content -Path $WorkflowFile -Value $Updated -Encoding utf8

Write-Host "Updated $WorkflowFile"
Write-Host "  Trigger mode: $Trigger"
Write-Host "  Primary: $PrimaryIst IST -> cron `"$PrimaryCron`""
Write-Host "  Fallback: $FallbackIst IST -> cron `"$FallbackCron`""
Write-Host ""

$RepoSlug = "namandeep-heer/gurudawara-agents"
$Origin = git config --get remote.origin.url 2>$null
if ($Origin -match 'github\.com[:/](.+?)(?:\.git)?$') {
    $RepoSlug = $Matches[1]
}

if ($Trigger -eq "cron_job_org") {
    Write-Host "cron-job.org setup (two daily jobs, timezone Asia/Kolkata):"
    Write-Host "  Primary:  $PrimaryIst"
    Write-Host "  Fallback: $FallbackIst"
    Write-Host ""
    Write-Host "  URL:    https://api.github.com/repos/$RepoSlug/actions/workflows/daily-hukamnama.yml/dispatches"
    Write-Host "  Method: POST"
    Write-Host "  Headers:"
    Write-Host "    Accept: application/vnd.github+json"
    Write-Host "    Authorization: Bearer <GITHUB_PAT with Actions: Read and write>"
    Write-Host "    X-GitHub-Api-Version: 2022-11-28"
    Write-Host '  Body:   {"ref":"main"}'
    Write-Host ""
    Write-Host "Store the PAT in cron-job.org only - not in GitHub Secrets."
} else {
    Write-Host "GitHub schedule mode enabled in workflow."
    Write-Host "Disable any cron-job.org jobs for this workflow to avoid duplicate triggers."
    Write-Host "Note: public-repo schedule runs may be delayed by up to ~60 minutes."
}

Write-Host ""
Write-Host "Next: git add $WorkflowFile $TriggerEnv; git commit; git push"