param(
    [Parameter(Mandatory = $false)]
    [string]$Repo = "."
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path $Repo).Path

$stylesPath = Join-Path $repoPath "src\classcatalog\static\styles.css"
$indexPath = Join-Path $repoPath "src\classcatalog\static\index.html"
$payloadPath = Join-Path $repoPath "classcatalog-admin-inbox-ops-patch\payload\inbox_admin.css.inc"

if (-not (Test-Path $stylesPath)) {
    throw "Could not find: $stylesPath"
}
if (-not (Test-Path $indexPath)) {
    throw "Could not find: $indexPath"
}

$marker = "/* Inbox bell desktop alignment v1 */"
$cssBlock = @'

/* Inbox bell desktop alignment v1 */
@media (min-width: 701px) {
  .header-actions {
    flex: 1 1 auto;
  }

  #inbox-nav {
    order: 999;
    margin-left: auto;
  }
}
'@

$changed = New-Object System.Collections.Generic.List[string]

function Add-CssBlockIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $false
    }

    $text = [System.IO.File]::ReadAllText($Path)
    if ($text.Contains($marker)) {
        return $false
    }

    $updated = $text.TrimEnd("`r", "`n") + $cssBlock + "`n"
    [System.IO.File]::WriteAllText(
        $Path,
        $updated,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $true
}

if (Add-CssBlockIfMissing -Path $stylesPath) {
    $changed.Add("src/classcatalog/static/styles.css")
}

if (Test-Path $payloadPath) {
    if (Add-CssBlockIfMissing -Path $payloadPath) {
        $changed.Add("classcatalog-admin-inbox-ops-patch/payload/inbox_admin.css.inc")
    }
}

$index = [System.IO.File]::ReadAllText($indexPath)
if (-not $index.Contains("bellright=1")) {
    $old = "/static/styles.css?"
    $new = "/static/styles.css?bellright=1&amp;"

    $count = ([regex]::Matches($index, [regex]::Escape($old))).Count
    if ($count -ne 1) {
        throw "Expected exactly one '$old' cache-buster anchor in index.html, found $count."
    }

    $index = $index.Replace($old, $new)
    [System.IO.File]::WriteAllText(
        $indexPath,
        $index,
        [System.Text.UTF8Encoding]::new($false)
    )
    $changed.Add("src/classcatalog/static/index.html")
}

if ($changed.Count -eq 0) {
    Write-Host "Inbox bell right-alignment patch is already applied; no changes made."
    exit 0
}

Write-Host "Inbox bell right-alignment patch applied successfully."
Write-Host "Changed files:"
foreach ($item in $changed) {
    Write-Host "  $item"
}
