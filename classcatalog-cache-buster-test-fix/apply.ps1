param(
    [Parameter(Mandatory = $false)]
    [string]$Repo = "."
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path $Repo).Path
$indexPath = Join-Path $repoPath "src\classcatalog\static\index.html"
$adminPatchPath = Join-Path $repoPath "classcatalog-admin-inbox-ops-patch\apply_admin_inbox_ops_patch.py"

if (-not (Test-Path $indexPath)) {
    throw "Could not find: $indexPath"
}

function Normalize-AssetQuery {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Html,
        [Parameter(Mandatory = $true)]
        [string]$AssetPattern
    )

    $pattern = "(/static/$AssetPattern\?)([^`"]+)"
    return [regex]::Replace(
        $Html,
        $pattern,
        {
            param($match)

            $prefix = $match.Groups[1].Value
            $query = $match.Groups[2].Value
            $tokens = @(
                [regex]::Split($query, '&(?:amp;)?') |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
            )

            $version = $tokens | Where-Object { $_ -match '^v=[^&]+$' } | Select-Object -First 1
            if (-not $version) {
                return $match.Value
            }

            $ordered = New-Object System.Collections.Generic.List[string]
            $ordered.Add($version)

            foreach ($token in $tokens) {
                if ($token -eq $version) {
                    continue
                }
                if (-not $ordered.Contains($token)) {
                    $ordered.Add($token)
                }
            }

            return $prefix + ($ordered -join '&amp;')
        },
        1
    )
}

$index = [System.IO.File]::ReadAllText($indexPath)
$updatedIndex = Normalize-AssetQuery -Html $index -AssetPattern 'styles\.css'
$updatedIndex = Normalize-AssetQuery -Html $updatedIndex -AssetPattern 'app\.js'

$changed = New-Object System.Collections.Generic.List[string]

if ($updatedIndex -ne $index) {
    [System.IO.File]::WriteAllText(
        $indexPath,
        $updatedIndex,
        [System.Text.UTF8Encoding]::new($false)
    )
    $changed.Add("src/classcatalog/static/index.html")
}

# Prevent the admin Inbox patch from putting "inboxops=1" before the version token
# on future fresh applications. Instead, append it at the end of the asset URL.
if (Test-Path $adminPatchPath) {
    $patchText = [System.IO.File]::ReadAllText($adminPatchPath)

    $oldBlock = @'
    # Cache-bust without depending on the exact accumulated query string.
    updated = updated.replace('/static/styles.css?', '/static/styles.css?inboxops=1&amp;', 1)
    updated = updated.replace('/static/app.js?', '/static/app.js?inboxops=1&amp;', 1)
'@

    $newBlock = @'
    # Cache-bust while preserving the version token immediately after "?".
    def append_asset_cache_token(source: str, asset: str, token: str) -> str:
        marker = f"/static/{asset}?"
        start = source.find(marker)
        if start == -1:
            return source
        end = source.find('"', start)
        if end == -1:
            return source
        segment = source[start:end]
        if token in segment:
            return source
        return source[:end] + f"&amp;{token}" + source[end:]

    updated = append_asset_cache_token(updated, "styles.css", "inboxops=1")
    updated = append_asset_cache_token(updated, "app.js", "inboxops=1")
'@

    if ($patchText.Contains($oldBlock)) {
        $patchText = $patchText.Replace($oldBlock, $newBlock)
        [System.IO.File]::WriteAllText(
            $adminPatchPath,
            $patchText,
            [System.Text.UTF8Encoding]::new($false)
        )
        $changed.Add("classcatalog-admin-inbox-ops-patch/apply_admin_inbox_ops_patch.py")
    }
}

$finalIndex = [System.IO.File]::ReadAllText($indexPath)
if ($finalIndex -notmatch '/static/styles\.css\?v=') {
    throw "styles.css version token is still not first in index.html."
}
if ($finalIndex -notmatch '/static/app\.js\?v=') {
    throw "app.js version token is still not first in index.html."
}

if ($changed.Count -eq 0) {
    Write-Host "Cache-buster ordering fix is already applied; no changes made."
} else {
    Write-Host "Cache-buster ordering fix applied successfully."
    Write-Host "Changed files:"
    foreach ($item in $changed) {
        Write-Host "  $item"
    }
}
