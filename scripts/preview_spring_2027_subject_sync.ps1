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
if ($Baseline -eq 'November') {
    $active = Join-Path $repo 'src\classcatalog\data\sections.backup-20261105-145857.json'
} else {
    $active = Join-Path $out 'baseline-live-sections.json'
}
foreach ($path in @($py, $incoming, $checkpoint, $active)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required input: $path" }
}
$reviewArgs = @()
$suffix = ''
if ($ApprovedRekeys -ne '') {
    if (!(Test-Path -LiteralPath $ApprovedRekeys -PathType Leaf)) { throw "Missing approved-rekeys file: $ApprovedRekeys" }
    $reviewArgs = @('--approved-rekeys', $ApprovedRekeys)
    $suffix = '-approved'
}
$report = Join-Path $out ("cs-style-preview-v4-" + $Baseline.ToLowerInvariant() + $suffix + '.json')
if (Test-Path -LiteralPath $report) { throw "Preview exists. Refusing overwrite: $report" }
$output = & $py -m classcatalog.dataset.subject_sync `
    --active "$active" --incoming "$incoming" --checkpoint "$checkpoint" `
    --term 'Spring 2027' --expected-subjects 48 @reviewArgs
if ($LASTEXITCODE -ne 0) { throw 'Subject-sync preview failed. No data modified.' }
$jsonText = $output -join [Environment]::NewLine
$parsed = $jsonText | ConvertFrom-Json
[System.IO.File]::WriteAllText($report, $jsonText, [System.Text.UTF8Encoding]::new($false))
Write-Host "`n===== CS-STYLE V4 $Baseline PREVIEW ====="
Write-Host "Existing sections: $($parsed.original_sections)"
Write-Host "Preserved outside scope: $($parsed.preserved_outside_scope)"
Write-Host "Retained classes: $($parsed.retained_physical_classes)"
Write-Host "Added: $($parsed.added_physical_classes)"
Write-Host "Removed: $($parsed.removed_physical_classes)"
Write-Host "Stable IDs preserved: $($parsed.stable_ids_preserved)"
Write-Host "Identity conflicts: $($parsed.identity_conflict_count)"
Write-Host "Explicitly approved career rekeys: $($parsed.approved_identity_rekey_count)"
Write-Host "Unapproved identity conflicts: $($parsed.unapproved_identity_conflict_count)"
if ($parsed.identity_conflict_count -gt 0) {
    Write-Host "`n===== IDENTITY CHANGES (PREVIEW ONLY) ====="
    foreach ($conflict in $parsed.identity_conflicts) {
        Write-Host "Class $($conflict.schedule_number) / $($conflict.term_code)"
        Write-Host "  OLD: $($conflict.before.course_code) | crse_id=$($conflict.before.crse_id) offer=$($conflict.before.crse_offer_nbr) career=$($conflict.before.acad_career)"
        Write-Host "  NEW: $($conflict.after.course_code) | crse_id=$($conflict.after.crse_id) offer=$($conflict.after.crse_offer_nbr) career=$($conflict.after.acad_career)"
    }
}
Write-Host "`n===== REMAINING METADATA LOSSES ====="
$details = @()
if ($null -ne $parsed.metadata_regression_details) {
    $details = @($parsed.metadata_regression_details | Where-Object { $null -ne $_ })
}
Write-Host "Total unresolved field losses: $($details.Count)"
foreach ($item in $details) {
    Write-Host "  $($item.term_code) / class $($item.schedule_number) | $($item.field) | old=$($item.old_course_key) new=$($item.new_course_key) crse_id_changed=$($item.crse_id_changed) old_section=$($item.old_section_number) new_section=$($item.new_section_number)"
}
Write-Host "Historical meetings carried forward: $($parsed.metadata_carried_forward.meetings)"
Write-Host "Historical instructors carried forward: $($parsed.metadata_carried_forward.instructor)"
Write-Host "Historical section numbers carried forward on same-career classes: $($parsed.metadata_carried_forward.section_number)"
Write-Host "Historical section numbers explicitly restored for reviewed career changes: $($parsed.reviewed_career_rekey_section_numbers_carried)"
Write-Host "Candidate total: $($parsed.candidate_sections)"
Write-Host "Report: $report"
Write-Host 'applied = false (read-only)'
if ($parsed.unapproved_identity_conflict_count -gt 0 -or $details.Count -gt 0) {
    Write-Host 'NOTE: staging/apply remains blocked pending review.'
}
