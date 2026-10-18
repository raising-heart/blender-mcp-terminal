<#
.SYNOPSIS
  Installs the Claude Terminal add-on into Blender on Windows.

.DESCRIPTION
  Finds Blender (4.2 or newer), downloads the latest release zip from GitHub
  (unless -Zip is given), and installs and enables it with Blender's own
  "extension install-file" command. Blender must be closed while this runs.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Blender "D:\Blender 4.2\blender.exe"
  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Zip "$HOME\Downloads\claude_terminal-1.0.0.zip"
#>
param(
    [string]$Blender,   # path to blender.exe (found automatically if empty)
    [string]$Zip,       # path to the add-on zip (latest release is downloaded if empty)
    [switch]$Force      # install even if Blender is open (not recommended)
)
# Blender prints harmless warnings on stderr; failures are checked via exit codes.
$ErrorActionPreference = "Continue"
$Repo = "raising-heart/blender-mcp-terminal"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Fail($text) { Say "`n$text" Red; exit 1 }

function Get-BlenderVersion($exe) {
    $line = & $exe --version 2>$null | Select-String -Pattern '^Blender (\d+)\.(\d+)' | Select-Object -First 1
    if ($line) { return [version]("{0}.{1}" -f $line.Matches[0].Groups[1].Value, $line.Matches[0].Groups[2].Value) }
    return $null
}

function Find-Blenders {
    $found = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root) { $found += Get-ChildItem (Join-Path $root "Blender Foundation\Blender*\blender.exe") -ErrorAction SilentlyContinue }
    }
    foreach ($steam in @("${env:ProgramFiles(x86)}\Steam\steamapps\common\Blender\blender.exe",
                         "$env:ProgramFiles\Steam\steamapps\common\Blender\blender.exe")) {
        if (Test-Path $steam) { $found += Get-Item $steam }
    }
    $onPath = Get-Command blender.exe -ErrorAction SilentlyContinue
    if ($onPath) { $found += Get-Item $onPath.Source }
    return $found | Sort-Object FullName -Unique
}

Say "Claude Terminal installer`n" Cyan

# 1. Blender
if ($Blender) {
    if (-not (Test-Path $Blender)) { Fail "Blender not found at: $Blender" }
    $exe = (Get-Item $Blender).FullName
    $ver = Get-BlenderVersion $exe
} else {
    $choices = foreach ($b in Find-Blenders) { $v = Get-BlenderVersion $b.FullName; if ($v) { [pscustomobject]@{ Exe = $b.FullName; Version = $v } } }
    if (-not $choices) { Fail "Couldn't find Blender. Install Blender 4.2 or newer, or run again with -Blender `"<path to blender.exe>`"." }
    $best = $choices | Sort-Object Version -Descending | Select-Object -First 1
    if (@($choices).Count -gt 1) { Say ("Found: " + (($choices | ForEach-Object { "Blender $($_.Version)" }) -join ", ") + ". Using the newest.") }
    $exe = $best.Exe; $ver = $best.Version
}
if (-not $ver -or $ver -lt [version]"4.2") { Fail "Blender $ver is too old. Claude Terminal needs Blender 4.2 or newer." }
Say "Blender $ver  ($exe)" Green

# The bundled Python packages are built for Python 3.11 (Blender 4.2 to 4.5).
$py = Get-ChildItem (Split-Path $exe) -Recurse -Depth 4 -Filter python.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\python\\bin\\python\.exe$' } | Select-Object -First 1
if ($py) {
    $pyVer = (& $py.FullName -I -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
    if ($pyVer -ne "3.11") {
        Fail "This Blender uses Python $pyVer, but this release is built for Python 3.11 (Blender 4.2 to 4.5). See the README's Development section to build for your Blender."
    }
}

# 2. Blender must be closed, or it may overwrite the new setting when it quits.
# (tasklist, not Get-Process: it also sees Blender from inside sandboxed shells)
if (-not $Force -and ((tasklist /FI "IMAGENAME eq blender.exe" /NH) -match 'blender\.exe')) {
    Fail "Blender is open. Save your work, close Blender, then run this again."
}

# 3. The add-on zip
if (-not $Zip) {
    Say "Downloading the latest release..."
    try {
        $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing
    } catch { Fail "Couldn't reach GitHub: $($_.Exception.Message)" }
    $asset = $release.assets | Where-Object { $_.name -like "claude_terminal-*.zip" } | Select-Object -First 1
    if (-not $asset) { Fail "The latest release has no add-on zip." }
    $Zip = Join-Path $env:TEMP $asset.name
    try {
        Invoke-WebRequest $asset.browser_download_url -OutFile $Zip -UseBasicParsing
    } catch { Fail "Download failed: $($_.Exception.Message)" }
}
if (-not (Test-Path $Zip)) { Fail "Zip not found: $Zip" }
Say "Add-on zip: $Zip" Green

# 4. Install and enable with Blender's own command
Say "Installing into Blender..."
# No --factory-startup here: the install saves preferences, and they must be the user's own.
$log = & $exe --command extension install-file -r user_default -e $Zip 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE

# Blender prints "Add-on not loaded ... not a sub-module" on a first install even
# when it works, so check the result by starting Blender in the background.
Say "Checking that Blender loads it..."
$check = & $exe -b --python-expr "import bpy, sys; m = 'bl_ext.user_default.claude_terminal'; print('CT_CHECK', m in bpy.context.preferences.addons and m in sys.modules)" 2>&1 |
    ForEach-Object { "$_" } | Where-Object { $_ -match '^CT_CHECK' }
if ($code -ne 0 -or $check -ne 'CT_CHECK True') {
    $log | Where-Object { $_ -notmatch 'TBBmalloc' -and $_.Trim() } | ForEach-Object { Say "  $_" }
    Fail "The add-on didn't install correctly. Try the picture guide instead: https://github.com/$Repo/blob/main/docs/INSTALL.md"
}

# 5. Claude Code
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Say "`nNote: Claude Code wasn't found. Install it before using the terminal: https://code.claude.com/docs/en/setup" Yellow
}

Say "`nDone! Claude Terminal is installed and turned on." Green
Say "Next: open Blender and click Window > Open Claude Terminal."
Say "The first time, move the mouse over the terminal, press Down, then Enter to trust the folder."
