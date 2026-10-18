<#
  Rebuild the extension, install it into an isolated test profile and start a
  test Blender with the bridge on a known token. Your normal Blender settings
  are not touched (everything lives in $env:TEMP\cct\bl_user).

  -Simulate starts Blender with --enable-event-simulate (real input is
  ignored, tests send keys through the bridge).
#>
param([switch]$Simulate, [switch]$Fresh, [string]$OpenFile)
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot
$blender = "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
$t = Join-Path $env:TEMP "cct"
New-Item -ItemType Directory -Force $t | Out-Null

$pidFile = Join-Path $t "blender_pid.txt"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile
    Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
}

& (Join-Path $root "build.ps1") 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -match 'Built|error|Error' }

# Clean environment: behave like Blender started from the Start menu.
Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC|MCP_)' } |
    ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, $null, "Process") }
$env:BLENDER_USER_RESOURCES = Join-Path $t "bl_user"
if ($Fresh -and (Test-Path $env:BLENDER_USER_RESOURCES)) { Remove-Item $env:BLENDER_USER_RESOURCES -Recurse -Force }

$zip = Get-ChildItem (Join-Path $root "dist") -Filter *.zip | Sort-Object LastWriteTime | Select-Object -Last 1
& $blender --command extension install-file -r user_default -e $zip.FullName 2>&1 | Out-Null
& $blender -b --python-expr "import bpy; bpy.context.preferences.view.show_splash = False; bpy.ops.wm.save_userpref()" 2>&1 | Out-Null

$env:CT_TEST_TOKEN = "testtoken-123"
$env:CT_TEST_INFO = Join-Path $t "bridge_info.json"
Remove-Item $env:CT_TEST_INFO -ErrorAction SilentlyContinue
$bargs = @('-p', '40', '40', '1600', '950')
if ($Simulate) { $bargs += '--enable-event-simulate' }
if ($OpenFile) {
    # Start like a user would: open a .blend, no helper script. The bridge
    # must come up on its own (prefs.bridge_autostart).
    $bargs += $OpenFile
} else {
    $bargs += @('--python', (Join-Path $PSScriptRoot "boot_test.py"))
}
$p = Start-Process -FilePath $blender -ArgumentList $bargs -PassThru `
    -RedirectStandardOutput (Join-Path $t "blender_out.txt") -RedirectStandardError (Join-Path $t "blender_err.txt")
$p.Id | Set-Content $pidFile
for ($i = 0; $i -lt 60; $i++) { if (Test-Path $env:CT_TEST_INFO) { break }; Start-Sleep -Milliseconds 500 }
"Blender pid $($p.Id)"
Get-Content $env:CT_TEST_INFO -ErrorAction SilentlyContinue
