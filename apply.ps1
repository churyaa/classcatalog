param(
    [string]$RepoRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$required = @(
    "src\classcatalog\seo.py",
    "src\classcatalog\templates\seo\base.html",
    "src\classcatalog\templates\seo\subjects.html",
    "src\classcatalog\templates\seo\subject.html",
    "src\classcatalog\templates\seo\course.html",
    "src\classcatalog\static\seo.css",
    "tests\test_seo_landing_pages.py"
)

foreach ($relative in $required) {
    $target = Join-Path $RepoRoot $relative
    if (-not (Test-Path $target)) {
        throw "Missing $relative. Apply/merge the original SEO landing-page package before this compact-layout follow-up."
    }
}

$repositoryPath = Join-Path $RepoRoot "src\classcatalog\repository.py"
if (-not (Select-String -Path $repositoryPath -Pattern "def displayed_options\(" -Quiet)) {
    throw "The repository does not contain the SEO displayed_options projection. Apply the original SEO package first."
}

$sourceRoot = Join-Path $PSScriptRoot "files"
foreach ($relative in $required) {
    $source = Join-Path $sourceRoot $relative
    $target = Join-Path $RepoRoot $relative
    Copy-Item -Path $source -Destination $target -Force
    Write-Host "Updated $relative"
}

Push-Location $RepoRoot
try {
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "git diff --check failed."
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Compact SEO layout applied successfully."
Write-Host "Run .\test.ps1 from this package, or use the commands in README.md."
