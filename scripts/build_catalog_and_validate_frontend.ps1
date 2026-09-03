$ErrorActionPreference = "Stop"

$Python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$Project = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Project

& $Python -m playwright install chromium
& $Python -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  --all-programs `
  --headed `
  --pause-for-human `
  --install-api-data

& $Python -m classcatalog.frontend_validation.main `
  --data src\classcatalog\data\sections.json `
  --catalog-data src\classcatalog\data\catalog_mappings.json `
  --output-dir results\fall-2026-frontend-validation `
  --expected-sections 7035 `
  --expected-courses 2955 `
  --expected-physical-sections 7024 `
  --expected-active-subjects 121
