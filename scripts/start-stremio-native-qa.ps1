# Launch the official Stremio v5 Desktop shell against a QA WebUI.
# No executable changes, no DLL injection, no permanent config changes.
# Close existing Stremio windows manually before running.
[CmdletBinding()]
param(
    [Parameter()]
    [string]$WebUiUrl = 'https://192-168-1-201.519b6502d940.stremio.rocks:12470/',
    [Parameter()]
    [string]$StremioExe = ''
)
$ErrorActionPreference = 'Stop'
$parsed = $null
if (-not [Uri]::TryCreate($WebUiUrl, [UriKind]::Absolute, [ref]$parsed) -or
    $parsed.Scheme -ne 'https') {
    throw 'WebUiUrl must be an absolute HTTPS URL'
}
if (Get-Process -Name 'Stremio' -ErrorAction SilentlyContinue) {
    throw 'Close the running Stremio app before launching the isolated QA session.'
}
if (-not $StremioExe) {
    $locations = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Stremio\Stremio.exe'),
        (Join-Path $env:LOCALAPPDATA 'Stremio\Stremio.exe'),
        (Join-Path $env:ProgramFiles 'Stremio\Stremio.exe'),
        $(if (${env:ProgramFiles(x86)}) {
            Join-Path ${env:ProgramFiles(x86)} 'Stremio\Stremio.exe'
        })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    if ($locations.Count -eq 0) {
        throw 'Stremio executable not found. Pass -StremioExe with the full path.'
    }
    $StremioExe = $locations[0]
}
if (-not (Test-Path -LiteralPath $StremioExe -PathType Leaf)) {
    throw "Stremio executable not found: $StremioExe"
}
Write-Host "Starting Stremio Desktop using isolated QA WebUI: $WebUiUrl"
Write-Host 'This does not change the Core server or permanently reconfigure the app.'
Start-Process -FilePath $StremioExe -ArgumentList @('--webui-url', $WebUiUrl)
