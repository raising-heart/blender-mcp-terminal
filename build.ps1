<#
.SYNOPSIS
  Builds the Claude Terminal extension zip into .\dist

.EXAMPLE
  .\build.ps1
  .\build.ps1 -Blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" -UpdateWheels

  -UpdateWheels downloads the Python packages again for the Python version of
  the given Blender (needed when Blender's Python version changes, e.g. 5.x).
#>
param(
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    [switch]$UpdateWheels
)
# Blender and pip print warnings on stderr; failures are caught via $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$src = Join-Path $PSScriptRoot "claude_terminal"
$dist = Join-Path $PSScriptRoot "dist"

if (-not (Test-Path $Blender)) { throw "Blender not found: $Blender" }

if ($UpdateWheels) {
    $py = Get-ChildItem (Split-Path $Blender) -Recurse -Depth 4 -Filter python.exe |
        Where-Object { $_.FullName -match '\\python\\bin\\python\.exe$' } | Select-Object -First 1
    if (-not $py) { throw "Could not find Blender's bundled python.exe" }
    $ver = & $py.FullName -I -c "import sys; print('%d.%d' % sys.version_info[:2])"
    Write-Host "Downloading wheels for Python $ver"
    Remove-Item "$src\wheels\*.whl" -ErrorAction SilentlyContinue
    & $py.FullName -I -m pip download pyte "wcwidth==0.2.13" pywinpty --only-binary=:all: `
        --python-version $ver --platform win_amd64 --implementation cp -d "$src\wheels" `
        --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip download failed" }
    $lines = Get-ChildItem "$src\wheels\*.whl" | Sort-Object Name | ForEach-Object { '  "./wheels/' + $_.Name + '",' }
    $manifest = Join-Path $src "blender_manifest.toml"
    $text = [IO.File]::ReadAllText($manifest)
    $text = [regex]::Replace($text, '(?s)wheels = \[.*?\]', "wheels = [`n" + ($lines -join "`n") + "`n]")
    [IO.File]::WriteAllText($manifest, $text)
}

Get-ChildItem $src -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force $dist | Out-Null
& $Blender --factory-startup --command extension build --source-dir $src --output-dir $dist
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
Get-ChildItem $dist -Filter *.zip | Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { Write-Host "Built $($_.FullName)" }
