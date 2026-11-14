param(
    [ValidateSet('November','Production')][string]$Baseline = 'November',
    [string]$ApprovedRekeys = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
$out = Join-Path $repo 'results\spring-2027-full-subject-rescrape-all-v1'
$incoming = Join-Path $out 'built\sections.json'
$checkpoint = Join-Path $out 'checkpoint.json'
$reviewArgs = @()
$suffix = ''
if ($ApprovedRekeys -ne '') {
    if (!(Test-Path -LiteralPath $ApprovedRekeys -PathType Leaf)) { throw "Missing reviewed file: $ApprovedRekeys" }
    $reviewArgs = @('--approved-rekeys', $ApprovedRekeys)
    $suffix = '-approved'
}
$previewPath = Join-Path $out ("cs-style-preview-v4-" + $Baseline.ToLowerInvariant() + $suffix + '.json')
if ($Baseline -eq 'November') {
    $baselinePath = Join-Path $repo 'src\classcatalog\data\sections.backup-20261105-145857.json'
} else {
    $baselinePath = Join-Path $out 'baseline-live-sections.json'
}
if (!(Test-Path -LiteralPath $previewPath -PathType Leaf)) { throw 'Run the matching V4 preview first.' }
$preview = Get-Content -LiteralPath $previewPath -Raw | ConvertFrom-Json
if ($preview.applied -ne $false) { throw 'Expected read-only preview.' }
if ($preview.unapproved_identity_conflict_count -gt 0) { throw "Unapproved identity conflicts: $($preview.unapproved_identity_conflict_count)" }
$baselineHash = (Get-FileHash -LiteralPath $baselinePath -Algorithm SHA256).Hash
if ($baselineHash -ne $preview.active_sha256_before) { throw 'Baseline changed since preview; stop.' }
$incomingHash = (Get-FileHash -LiteralPath $incoming -Algorithm SHA256).Hash
if ($incomingHash -ne $preview.incoming_sha256) { throw 'Incoming changed since preview; stop.' }
if ($ApprovedRekeys -ne '') {
    $approvedHash = (Get-FileHash -LiteralPath $ApprovedRekeys -Algorithm SHA256).Hash
    if ($approvedHash -ne $preview.approved_rekeys_sha256) { throw 'Reviewed approvals changed since preview; stop.' }
} elseif ($preview.approved_identity_rekey_count -gt 0) {
    throw 'Approved rekey file is missing; stop.'
}
$regressions = 0
if ($null -ne $preview.metadata_regression_details) {
    $regressions = @($preview.metadata_regression_details | Where-Object { $null -ne $_ }).Count
}
if ($regressions -gt 0) { throw "$regressions metadata regressions remain. Stop and review." }
$stage = Join-Path $out ('cs-style-staging-v4-' + $Baseline.ToLowerInvariant())
if (Test-Path -LiteralPath $stage) { throw "Staging folder already exists: $stage" }
New-Item -Path $stage -ItemType Directory | Out-Null
$stagedActive = Join-Path $stage 'sections.json'
Copy-Item -LiteralPath $baselinePath -Destination $stagedActive
$stageHash = (Get-FileHash -LiteralPath $stagedActive -Algorithm SHA256).Hash
if ($stageHash -ne $baselineHash) { throw 'Staging baseline copy hash mismatch' }
Write-Host 'Applying subject-sync ONLY to disposable staging copy.'
$output = & $py -m classcatalog.dataset.subject_sync `
    --active "$stagedActive" --incoming "$incoming" --checkpoint "$checkpoint" `
    --term 'Spring 2027' --expected-subjects 48 --apply `
    --expected-active-sha256 "$baselineHash" --max-removals $preview.removed_physical_classes @reviewArgs
if ($LASTEXITCODE -ne 0) { throw 'Staging sync failed. Production was not modified.' }
$stageReport = Join-Path $stage 'sync-report.json'
[System.IO.File]::WriteAllText($stageReport, ($output -join [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
Write-Host "Staged dataset: $stagedActive"
Write-Host "Sync report: $stageReport"
Write-Host "`n===== API VALIDATION ====="
& $py -m classcatalog.api_validation.main --data "$stagedActive" --output-dir (Join-Path $stage 'api-validation')
if ($LASTEXITCODE -ne 0) { throw 'API validation failed. Do not deploy.' }
Write-Host 'Staged dataset validated. Production was not modified.'
