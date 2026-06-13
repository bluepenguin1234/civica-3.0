# run_pipeline.ps1 — Civica Signals scheduled data refresh.
#
# Registered in Windows Task Scheduler (daily 07:00) so the live site always
# carries the latest public-record data: crawl -> ocr -> extract -> link ->
# VALIDATE -> publish -> push. The validation gate is the safety: if it goes
# red, nothing is committed or pushed and the site keeps serving the last
# good feed. Only docs/output/signals/ is auto-committed — code and page
# changes always go through a human.
#
# Requirements: python + git + claude (Claude Code CLI, logged in) on PATH
# for the user the task runs as. Log: signals\pipeline_run.log (gitignored).

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot   # signals\ -> repo root
Set-Location $repo
$log = Join-Path $PSScriptRoot 'pipeline_run.log'
Start-Transcript -Path $log -Append | Out-Null
Write-Output "=== Signals refresh $(Get-Date -Format 'yyyy-MM-dd HH:mm') ==="

function Run-Step($name, $module) {
    Write-Output "--- $name ---"
    python -m $module
    if ($LASTEXITCODE -ne 0) {
        Write-Output "STEP FAILED: $name (exit $LASTEXITCODE)"
        return $false
    }
    return $true
}

# Collection steps: a failure here is logged but doesn't block publishing
# whatever DID land (per-town/per-doc errors are already isolated inside).
Run-Step 'crawl'     'signals.crawl.crawl'             | Out-Null
Run-Step 'ocr'       'signals.extract.ocr'             | Out-Null
Run-Step 'extract'   'signals.extract.extract'         | Out-Null
Run-Step 'link'      'signals.link.link_stories'       | Out-Null
Run-Step 'briefs'    'signals.synthesize.build_briefs' | Out-Null
Run-Step 'entities'  'signals.enrich.resolve_entities' | Out-Null

# The gate: red means STOP — do not publish, site keeps the last good data.
if (-not (Run-Step 'validate' 'signals.validate_signals')) {
    Write-Output 'VALIDATION RED -> not publishing. Fix and rerun.'
    Stop-Transcript | Out-Null
    exit 1
}

if (-not (Run-Step 'publish' 'signals.publish.build_signals_json')) {
    Stop-Transcript | Out-Null
    exit 1
}

# Push ONLY the published data files, and only if they actually changed.
git add docs/output/signals
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "signals: data refresh $(Get-Date -Format 'yyyy-MM-dd')"
    git push
    if ($LASTEXITCODE -eq 0) { Write-Output 'Published to GitHub Pages.' }
    else { Write-Output 'PUSH FAILED - data committed locally only.' }
} else {
    Write-Output 'No data changes - nothing to publish.'
}

Write-Output "=== done $(Get-Date -Format 'yyyy-MM-dd HH:mm') ==="
Stop-Transcript | Out-Null
