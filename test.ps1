param(
    [string]$RepoRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment Python not found at $python"
}

Push-Location $RepoRoot
try {
    & $python -m pytest -q `
        tests\test_seo.py `
        tests\test_seo_landing_pages.py `
        tests\test_cookie_consent.py `
        tests\test_filters.py `
        tests\test_seat_refresh.py `
        tests\test_seat_refresh_api.py `
        tests\test_ui_features.py
    if ($LASTEXITCODE -ne 0) { throw "Focused regression tests failed." }

    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff failed." }

    uv run ty check
    if ($LASTEXITCODE -ne 0) { throw "ty failed." }

    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Full test suite failed." }
}
finally {
    Pop-Location
}
