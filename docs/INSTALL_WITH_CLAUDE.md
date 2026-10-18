# Let Claude install it for you

If you already use Claude Code, it can do the whole install.

## What to do

1. Save your work in Blender and **close Blender**.
2. Open Claude Code: type `claude` in PowerShell, or use the Code tab in the
   Claude desktop app.
3. Copy this message, paste it to Claude, and press Enter:

   ```text
   Please install the Claude Terminal add-on for Blender on this computer.
   Follow the instructions for Claude in this guide:
   https://github.com/raising-heart/blender-mcp-terminal/blob/main/docs/INSTALL_WITH_CLAUDE.md
   ```

4. Claude may ask permission to download and run the installer. Say yes.
5. When Claude asks, open Blender the way you normally do, so it can check the
   install worked.

If anything goes wrong, Claude will walk you through the
[picture guide](INSTALL.md) instead.

---

## Instructions for Claude

You're installing the Claude Terminal add-on into the user's own Blender, so it
loads when they start Blender from the Start menu or taskbar. Explain each step
in plain words; the user may not be technical.

### 1. Check the computer

- It must be Windows. If not, stop and say this release is Windows only.
- Run `claude --version`. If it fails, help the user install Claude Code first:
  https://code.claude.com/docs/en/setup
- Ask the user to save their work and close Blender. Installing while Blender is
  open can be undone when Blender quits.

### 2. Download the installer, read it, run it

```powershell
$script = Join-Path $env:TEMP "claude_terminal_install.ps1"
Invoke-WebRequest "https://raw.githubusercontent.com/raising-heart/blender-mcp-terminal/main/install.ps1" -OutFile $script -UseBasicParsing
Get-Content $script   # read it before running
powershell -NoProfile -ExecutionPolicy Bypass -File $script
```

`-ExecutionPolicy Bypass` applies to this one run only. The script finds
Blender 4.2 or newer, checks Blender's Python is 3.11, downloads the latest
release zip, and installs it with Blender's own
`blender --command extension install-file -r user_default -e <zip>`.

- If it reports several Blender versions, it uses the newest. Use
  `-Blender "<path to blender.exe>"` if the user wants a different one.
- If it says Blender is open, ask the user to close it and run it again.

### 3. Check it worked, from the user's side

Some Claude setups run commands in a sandbox. Files written to `%APPDATA%` from
inside it can be invisible to programs the user starts. So don't trust the
install output alone:

1. Ask the user to open Blender the way they normally do.
2. Then run:

   ```powershell
   Get-NetTCPConnection -LocalPort 9877 -State Listen -ErrorAction SilentlyContinue |
       ForEach-Object { Get-Process -Id $_.OwningProcess } | Select-Object Id, ProcessName
   ```

   A `blender` process on port 9877 means the add-on loaded. Its bridge starts by
   itself with Blender.

If nothing is listening, the install didn't reach the user's Blender. **Don't try
to get around a sandbox.** Instead:

1. Put the zip somewhere the user can see, such as the folder you're working in
   (download it from https://github.com/raising-heart/blender-mcp-terminal/releases/latest).
2. Tell the user where it is, and walk them through
   [the picture guide](https://github.com/raising-heart/blender-mcp-terminal/blob/main/docs/INSTALL.md)
   from Step 2.
3. Check port 9877 again afterwards.

### 4. Tell the user how to start

- In Blender: **Window > Open Claude Terminal**.
- The first time, Claude Code asks whether to trust the folder. The arrow starts
  on "No, exit": move the mouse over the terminal, press **Down**, then **Enter**.
- Keys go to the terminal only while the mouse is over it.
