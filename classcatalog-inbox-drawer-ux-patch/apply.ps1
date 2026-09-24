param(
    [Parameter(Mandatory = $false)]
    [string]$Repo = "."
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path $Repo).Path

$appPath = Join-Path $repoPath "src\classcatalog\static\app.js"
$stylesPath = Join-Path $repoPath "src\classcatalog\static\styles.css"
$indexPath = Join-Path $repoPath "src\classcatalog\static\index.html"
$payloadJsPath = Join-Path $repoPath "classcatalog-admin-inbox-ops-patch\payload\inbox.js.inc"
$payloadCssPath = Join-Path $repoPath "classcatalog-admin-inbox-ops-patch\payload\inbox_admin.css.inc"

foreach ($required in @($appPath, $stylesPath, $indexPath)) {
    if (-not (Test-Path $required)) {
        throw "Could not find expected ClassCatalog file: $required"
    }
}

$jsMarker = "// Inbox drawer outside-click behavior v1"
$jsBlock = @'

// Inbox drawer outside-click behavior v1
document.addEventListener("click", (event) => {
  if (!state.inboxDrawerOpen) return;

  const target = event.target;
  if (!(target instanceof Element)) return;

  if (target.closest("#inbox-drawer") || target.closest("#inbox-nav")) return;
  closeInboxDrawer();
});
'@

$cssMarker = "/* Compact Inbox drawer v1 */"
$cssBlock = @'

/* Compact Inbox drawer v1 */
.inbox-drawer {
  width: min(340px, calc(100vw - 24px));
  max-width: 340px;
  min-width: 0;
}
'@

$changed = New-Object System.Collections.Generic.List[string]

function Append-BlockIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Marker,
        [Parameter(Mandatory = $true)]
        [string]$Block
    )

    if (-not (Test-Path $Path)) {
        return $false
    }

    $text = [System.IO.File]::ReadAllText($Path)
    if ($text.Contains($Marker)) {
        return $false
    }

    $updated = $text.TrimEnd("`r", "`n") + $Block + "`n"
    [System.IO.File]::WriteAllText(
        $Path,
        $updated,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $true
}

function Append-AssetCacheToken {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Html,
        [Parameter(Mandatory = $true)]
        [string]$Asset,
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $marker = "/static/$Asset?"
    $start = $Html.IndexOf($marker, [System.StringComparison]::Ordinal)
    if ($start -lt 0) {
        throw "Could not find $marker in index.html"
    }

    $end = $Html.IndexOf('"', $start)
    if ($end -lt 0) {
        throw "Could not find the end of the $Asset asset URL in index.html"
    }

    $segment = $Html.Substring($start, $end - $start)
    if ($segment.Contains($Token)) {
        return $Html
    }

    return $Html.Substring(0, $end) + "&amp;$Token" + $Html.Substring($end)
}

if (Append-BlockIfMissing -Path $appPath -Marker $jsMarker -Block $jsBlock) {
    $changed.Add("src/classcatalog/static/app.js")
}

if (Append-BlockIfMissing -Path $stylesPath -Marker $cssMarker -Block $cssBlock) {
    $changed.Add("src/classcatalog/static/styles.css")
}

# Keep the original admin Inbox patch payloads in sync so a future fresh
# application of that patch gets the same behavior and sizing.
if (Test-Path $payloadJsPath) {
    if (Append-BlockIfMissing -Path $payloadJsPath -Marker $jsMarker -Block $jsBlock) {
        $changed.Add("classcatalog-admin-inbox-ops-patch/payload/inbox.js.inc")
    }
}

if (Test-Path $payloadCssPath) {
    if (Append-BlockIfMissing -Path $payloadCssPath -Marker $cssMarker -Block $cssBlock) {
        $changed.Add("classcatalog-admin-inbox-ops-patch/payload/inbox_admin.css.inc")
    }
}

$index = [System.IO.File]::ReadAllText($indexPath)
$updatedIndex = Append-AssetCacheToken -Html $index -Asset "styles.css" -Token "inboxdrawer=1"
$updatedIndex = Append-AssetCacheToken -Html $updatedIndex -Asset "app.js" -Token "inboxdrawer=1"

if ($updatedIndex -ne $index) {
    [System.IO.File]::WriteAllText(
        $indexPath,
        $updatedIndex,
        [System.Text.UTF8Encoding]::new($false)
    )
    $changed.Add("src/classcatalog/static/index.html")
}

# Basic safety checks.
$finalApp = [System.IO.File]::ReadAllText($appPath)
$finalCss = [System.IO.File]::ReadAllText($stylesPath)
$finalIndex = [System.IO.File]::ReadAllText($indexPath)

if (-not $finalApp.Contains($jsMarker)) {
    throw "Outside-click Inbox behavior was not installed."
}
if (-not $finalCss.Contains($cssMarker)) {
    throw "Compact Inbox drawer CSS was not installed."
}
if (-not $finalIndex.Contains("inboxdrawer=1")) {
    throw "Asset cache-buster was not installed."
}
if ($finalIndex -notmatch '/static/styles\.css\?v=') {
    Write-Warning "styles.css does not currently have v= as its first query parameter."
}
if ($finalIndex -notmatch '/static/app\.js\?v=') {
    Write-Warning "app.js does not currently have v= as its first query parameter."
}

if ($changed.Count -eq 0) {
    Write-Host "Inbox drawer UX patch is already applied; no changes made."
} else {
    Write-Host "Inbox drawer UX patch applied successfully."
    Write-Host "Changed files:"
    foreach ($item in $changed) {
        Write-Host "  $item"
    }
}

Write-Host ""
Write-Host "Behavior:"
Write-Host "  - Clicking anywhere outside the Inbox drawer closes it."
Write-Host "  - Clicking the Inbox bell itself does not immediately close it."
Write-Host "  - The drawer is capped at 340px wide."
