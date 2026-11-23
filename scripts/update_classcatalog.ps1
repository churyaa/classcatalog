<#[
.SYNOPSIS
  Preview, approve, deploy, or check a ClassCatalog Spring 2027 dataset update.
.DESCRIPTION
  PREVIEW: download *actual* /var/lib/classcatalog/sections.json, invoke the existing
  V4 subject_sync module in a disposable staging copy, run local integrity and API tests.
  DEPLOY: require explicit APPROVE decisions for every proposed removal and an
  -Execute switch; verify active and candidate hashes, upload, launch root systemd
  deployment with lightweight checks and rollback. It does not auto-approve changes.
  STATUS: inspect the systemd deployment unit, active SHA, and live API.

  Requires the V4 subject_sync module already installed in the ClassCatalog repo.
  Spring 2027 only: existing V4 engine does not yet support every term code.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Preview','Deploy','Status')]
    [string]$Mode,
    [string]$SourceDir,
    [string]$RunDir,
    [int]$ExpectedSubjects=48,
    [ValidateSet('Spring 2027')]
    [string]$Term='Spring 2027',
    [switch]$Execute,
    [string]$Server='ubuntu@170.9.8.162',
    [string]$SshKey="$HOME\.ssh\classcatalog_desktop"
)

$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
$checks = Join-Path $PSScriptRoot 'classcatalog_update_checks.py'
$deployScript = Join-Path $PSScriptRoot 'classcatalog_deploy_lightweight.sh'
$activePath='/var/lib/classcatalog/sections.json'

foreach ($path in @($py,$checks)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing prerequisite: $path" }
}
if (-not (Test-Path -LiteralPath $SshKey)) { throw "SSH key missing: $SshKey" }

function Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function RemoteSha([string]$path) {
    $stdout = & ssh -i $SshKey -o IdentitiesOnly=yes -o ConnectTimeout=20 $Server "sha256sum '$path'"
    if ($LASTEXITCODE -ne 0) { throw "SSH failed verifying remote checksum: $path" }
    $line = ($stdout -join "`n").Trim()
    if ($line -notmatch '^([0-9a-fA-F]{64})\s') { throw "Unexpected remote checksum output: $line" }
    return $Matches[1].ToLowerInvariant()
}
function NewRunId {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $suffix = [Guid]::NewGuid().ToString('N').Substring(0,8)
    return "$stamp-$suffix"
}
function ReadJson([string]$path) {
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

if ($Mode -eq 'Preview') {
    if (-not $SourceDir) { throw 'Preview requires -SourceDir pointing to completed scrape output.' }
    if ($Execute) { throw 'Preview never accepts -Execute.' }
    $source = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $SourceDir))
    $incoming = Join-Path $source 'built\sections.json'
    $checkpoint = Join-Path $source 'checkpoint.json'
    foreach ($item in @($incoming,$checkpoint)) {
        if (-not (Test-Path $item)) { throw "Expected completed scrape file missing: $item" }
    }
    $runId=NewRunId
    $run=Join-Path $repo "results\classcatalog-update-runs\$runId"
    if (Test-Path $run) { throw 'Run directory already exists; refusing overwrite.' }
    New-Item -ItemType Directory -Path $run -Force | Out-Null
    $baseline=Join-Path $run 'baseline-live.json'
    $candidate=Join-Path $run 'candidate-sections.json'
    $previewPath=Join-Path $run 'preview.json'
    $applyReport=Join-Path $run 'staging-apply.json'
    $review=Join-Path $run 'removals.csv'
    $apiDir=Join-Path $run 'api-validation'

    Write-Host '===== DOWNLOAD ACTUAL LIVE BASELINE ====='
    $before=RemoteSha $activePath
    & scp -O -i $SshKey -o IdentitiesOnly=yes "${Server}:$activePath" $baseline
    if ($LASTEXITCODE -ne 0) { throw 'SCP baseline download failed.' }
    if (-not (Test-Path $baseline)) { throw 'Downloaded baseline is missing.' }
    $downloaded=Sha256 $baseline
    $after=RemoteSha $activePath
    if ($before -ne $downloaded -or $after -ne $downloaded) {
        throw 'Production dataset changed during download. Stop and retry with a new run.'
    }

    Write-Host '===== READ-ONLY RECONCILIATION PREVIEW ====='
    $previewOutput = & $py -m classcatalog.dataset.subject_sync `
        --active $baseline --incoming $incoming --checkpoint $checkpoint `
        --term $Term --expected-subjects $ExpectedSubjects
    if ($LASTEXITCODE -ne 0) { throw 'V4 preview failed. No active datasets modified.' }
    $previewText=$previewOutput -join [Environment]::NewLine
    $previewObject=$previewText | ConvertFrom-Json -ErrorAction Stop
    if (-not $previewObject -or $previewObject.applied -ne $false) { throw 'Unexpected preview response.' }
    [IO.File]::WriteAllText($previewPath,$previewText,[Text.UTF8Encoding]::new($false))

    Write-Host '===== BUILD EXACT REMOVAL REVIEW ====='
    & $py $checks review --baseline $baseline --incoming $incoming --preview $previewPath --review $review
    if ($LASTEXITCODE -ne 0) { throw 'Safety review failed; do not stage/deploy.' }

    Write-Host '===== APPLY TO DISPOSABLE LOCAL STAGING COPY ====='
    Copy-Item -LiteralPath $baseline -Destination $candidate
    $maxRemovals=[int]$previewObject.removed_physical_classes
    $applyOutput = & $py -m classcatalog.dataset.subject_sync `
        --active $candidate --incoming $incoming --checkpoint $checkpoint `
        --term $Term --expected-subjects $ExpectedSubjects `
        --apply --expected-active-sha256 $downloaded --max-removals $maxRemovals
    if ($LASTEXITCODE -ne 0) { throw 'Staging merge failed. Production unchanged.' }
    $applyText=$applyOutput -join [Environment]::NewLine
    $applyObject=$applyText | ConvertFrom-Json -ErrorAction Stop
    if ($applyObject.applied -ne $true -or $applyObject.candidate_sections -ne $previewObject.candidate_sections) {
        throw 'Staging output differs from preview. Production unchanged.'
    }
    [IO.File]::WriteAllText($applyReport,$applyText,[Text.UTF8Encoding]::new($false))

    Write-Host '===== VERIFY ALL NON-TARGET TERMS ARE UNCHANGED ====='
    $verifyOutput = & $py $checks verify --baseline $baseline --incoming $incoming --candidate $candidate `
        --preview $previewPath --review $review --term $Term
    if ($LASTEXITCODE -ne 0) { throw 'Local candidate integrity check failed.' }
    $verifyReport = ($verifyOutput -join [Environment]::NewLine) | ConvertFrom-Json

    Write-Host '===== API VALIDATION ====='
    & $py -m classcatalog.api_validation.main --data $candidate --output-dir $apiDir
    if ($LASTEXITCODE -ne 0) { throw 'API validation failed; do not deploy.' }
    $apiReport=Join-Path $apiDir 'api-validation-report.json'
    $api=ReadJson $apiReport
    if ($api.status -ne 'passed' -or [int]$api.errors -ne 0 -or [int]$api.warnings -ne 0) {
        throw 'API validation did not pass cleanly.'
    }
    if ([int]$api.counts.course_section_listings -ne [int]$previewObject.candidate_sections) {
        throw 'API validation section count does not match preview.'
    }

    $manifest=[ordered]@{
        run_id=$runId; term=$Term; source=$source; baseline=$baseline; incoming=$incoming;
        candidate=$candidate; checkpoint=$checkpoint; preview=$previewPath;
        removal_review=$review; api_report=$apiReport;
        expected_subjects=$ExpectedSubjects;
        baseline_sha256=$downloaded; candidate_sha256=(Sha256 $candidate);
        candidate_sections=[int]$previewObject.candidate_sections;
        term_count=@($verifyReport.term_counts.PSObject.Properties).Count;
        created_utc=[DateTime]::UtcNow.ToString('o')
    }
    $manifestPath=Join-Path $run 'manifest.json'
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    Write-Host ''
    Write-Host '===== PREVIEW COMPLETE ====='
    Write-Host "RUN_DIR=$run"
    Write-Host "BASELINE=$($previewObject.original_sections) CANDIDATE=$($manifest.candidate_sections)"
    Write-Host "ADDED=$($previewObject.added_physical_classes) REMOVED=$maxRemovals"
    Write-Host 'API validation passed. Production unchanged.'
    Write-Host "REVIEW: $review"
    Write-Host 'Set each removal decision to APPROVE after review, then run -Mode Deploy -RunDir ... -Execute.'
    exit 0
}

if (-not $RunDir) { throw "-Mode $Mode requires -RunDir." }
$runFull=[IO.Path]::GetFullPath((Join-Path (Get-Location).Path $RunDir))
$manifestPath=Join-Path $runFull 'manifest.json'
if (-not (Test-Path $manifestPath)) { throw "Manifest not found: $manifestPath" }
$manifest=ReadJson $manifestPath
if ($manifest.term -ne 'Spring 2027') { throw 'This V4 automation currently supports Spring 2027 only.' }

if ($Mode -eq 'Deploy') {
    if (-not $Execute) { throw 'No deployment executed. Specify -Execute after reviewing removal CSV.' }
    if (-not (Test-Path $deployScript)) { throw "Missing deployment script: $deployScript" }
    $api=ReadJson $manifest.api_report
    if ($api.status -ne 'passed' -or [int]$api.errors -ne 0 -or [int]$api.warnings -ne 0 -or
        [int]$api.counts.course_section_listings -ne [int]$manifest.candidate_sections) {
        throw 'Saved API validation report is not clean.'
    }
    if ((Sha256 $manifest.baseline) -ne $manifest.baseline_sha256 -or
        (Sha256 $manifest.candidate) -ne $manifest.candidate_sha256) {
        throw 'Saved baseline or candidate changed after validation.'
    }
    Write-Host '===== VERIFY EXACT LOCAL REMOVAL APPROVALS ====='
    & $py $checks verify --baseline $manifest.baseline --incoming $manifest.incoming `
        --candidate $manifest.candidate --preview $manifest.preview --review $manifest.removal_review `
        --term $manifest.term --approve
    if ($LASTEXITCODE -ne 0) { throw 'Removal approvals or candidate integrity check failed.' }
    Write-Host '===== VERIFY CURRENT LIVE PRODUCTION ====='
    if ((RemoteSha $activePath) -ne $manifest.baseline_sha256) {
        throw 'Production changed after preview. Start a new Preview run instead of deploying.'
    }
    $id=[string]$manifest.run_id
    if ($id -notmatch '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') { throw 'Unexpected run ID.' }
    $remoteCandidate="/tmp/classcatalog-update-$id.json"
    $remoteDeploy="/tmp/classcatalog-update-$id.sh"
    $unit="classcatalog-update-$id"
    $localScriptHash=Sha256 $deployScript
    # Refuse to overwrite an existing remote candidate or script from another attempt.
    Write-Host '===== UPLOAD VERIFIED FILES ====='
    $remoteProbe="if test -e '$remoteCandidate' || test -e '$remoteDeploy'; then echo EXISTS; else echo ABSENT; fi"
    $check=& ssh -i $SshKey -o IdentitiesOnly=yes $Server $remoteProbe
    if ($LASTEXITCODE -ne 0) { throw 'SSH connection failed while checking remote destination.' }
    if (($check -join "`n").Trim() -ne 'ABSENT') { throw 'Remote deployment files already exist. Inspect rather than overwrite.' }
    & scp -i $SshKey -o IdentitiesOnly=yes $manifest.candidate "${Server}:$remoteCandidate"
    if ($LASTEXITCODE -ne 0) { throw 'Candidate upload failed.' }
    & scp -i $SshKey -o IdentitiesOnly=yes $deployScript "${Server}:$remoteDeploy"
    if ($LASTEXITCODE -ne 0) { throw 'Deployment script upload failed.' }
    if ((RemoteSha $remoteCandidate) -ne $manifest.candidate_sha256 -or
        (RemoteSha $remoteDeploy) -ne $localScriptHash) {
        throw 'Uploaded file checksum mismatch.'
    }
    $syntax=& ssh -i $SshKey -o IdentitiesOnly=yes $Server "bash -n '$remoteDeploy'"
    if ($LASTEXITCODE -ne 0) { throw 'Remote Bash syntax check failed.' }
    if ((RemoteSha $activePath) -ne $manifest.baseline_sha256) {
        throw 'Production changed during upload. Do not launch.'
    }
    Write-Host '===== LAUNCH PROTECTED ROOT SYSTEMD DEPLOYMENT ====='
    $cmd="sudo -n systemd-run --unit=$unit --property=User=root /bin/bash '$remoteDeploy' '$($manifest.baseline_sha256)' '$($manifest.candidate_sha256)' '$($manifest.candidate_sections)' '$($manifest.term_count)' '$remoteCandidate'"
    $result=& ssh -i $SshKey -o IdentitiesOnly=yes $Server $cmd
    if ($LASTEXITCODE -ne 0) { throw 'Could not launch remote deployment. Inspect before retrying.' }
    $result | ForEach-Object { Write-Host $_ }
    [IO.File]::WriteAllText((Join-Path $runFull 'deployment-unit.txt'),$unit,[Text.UTF8Encoding]::new($false))
    Write-Host "DEPLOYMENT_LAUNCHED unit=$unit"
    Write-Host "Run -Mode Status -RunDir '$runFull' to inspect final result. Launch != success."
    exit 0
}

if ($Mode -eq 'Status') {
    $unitFile=Join-Path $runFull 'deployment-unit.txt'
    if (-not (Test-Path $unitFile)) { throw 'No deployment unit recorded for this run.' }
    $unit=(Get-Content $unitFile -Raw).Trim()
    if ($unit -notmatch '^classcatalog-update-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') {
        throw 'Unexpected deployment unit name.'
    }
    $cmd="systemctl show '$unit.service' -p ActiveState -p SubState -p Result -p ExecMainStatus --no-pager; sudo -n journalctl -u '$unit.service' -n 80 --no-pager; sha256sum '$activePath'; curl -fsS --max-time 15 http://127.0.0.1:8000/api/health"
    & ssh -i $SshKey -o IdentitiesOnly=yes $Server $cmd
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect final deployment state.' }
    Write-Host "Expected SHA256: $($manifest.candidate_sha256)"
    Write-Host "Expected sections: $($manifest.candidate_sections)"
}
