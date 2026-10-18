# Claude Terminal for Blender

A Blender add-on that puts a terminal inside Blender, runs Claude Code in it, and
connects Claude to the open scene through a built-in MCP bridge. You type to Claude
in one panel and watch the 3D Viewport change next to it.

![Claude Code running in a terminal panel next to Blender's 3D Viewport](docs/images/claude-terminal-in-blender.png)

Two things in one add-on:

- **The terminal:** Claude Code running in a panel inside Blender.
- **The MCP bridge:** gives Claude tools to read the scene, run Python in Blender
  and take viewport screenshots. Claude Code outside Blender can use it too
  (see [below](#use-claude-from-outside-blender-too)).

Requirements:

- Works with Blender 4.2 or newer on Windows.
- Needs Claude Code installed and logged in (`claude` works in a normal terminal).
- No other installs: the add-on brings its own Python packages, and the MCP server
  runs on Blender's bundled Python.

## Install

Pick whichever is easiest for you:

- **[Step-by-step picture guide](docs/INSTALL.md):** about 5 minutes, no coding.
- **[Let Claude install it for you](docs/INSTALL_WITH_CLAUDE.md):** paste one
  message into Claude Code and it does the rest.
- **One command** (close Blender first), in PowerShell:

  ```powershell
  $s = "$env:TEMP\claude_terminal_install.ps1"; iwr https://raw.githubusercontent.com/raising-heart/blender-mcp-terminal/main/install.ps1 -OutFile $s -UseBasicParsing; powershell -NoProfile -ExecutionPolicy Bypass -File $s
  ```

  [`install.ps1`](install.ps1) finds Blender, downloads the latest release and
  installs it with Blender's own extension installer.

## Use

1. In the top bar, click **Window > Open Claude Terminal**. (Also in the 3D
   Viewport sidebar: press **N**, open the **Claude** tab.)
2. The 3D Viewport splits and the terminal opens on the right with Claude Code
   already running and connected to Blender.
3. **Move the mouse over the terminal to type into it.** Like every Blender
   editor, keys go to the area under the mouse.
4. The first time in a folder, Claude Code asks whether you trust it. The arrow
   starts on "No, exit", so press **Down**, then **Enter**.

Things to try:

> Look at the scene, then build a low-poly chibi character in a T-pose from the
> reference sheets in this folder. Work one body part at a time and check each
> step with a viewport screenshot.

Claude gets these tools:

| Tool | What it does |
| --- | --- |
| `get_scene_info` | Objects, transforms, sizes, materials, selection, mode, units |
| `get_object_info` | One object in detail: bounding box, modifiers, materials, mesh stats |
| `execute_python` | Runs Python (bpy / bmesh) inside Blender; each change is one undo step |
| `get_viewport_screenshot` | Captures the 3D Viewport, optionally from Front/Side/Top first |

Press **Ctrl+Z** (with the mouse over the viewport or the terminal) to undo
Claude's last change.

### Keys in the terminal

| Keys | Action |
| --- | --- |
| Typing, arrows, Tab, Enter, Esc, Ctrl+letter | Go to the terminal as usual |
| Shift+Tab | Cycle Claude Code's permission mode |
| Shift+Enter | New line in Claude's prompt |
| Ctrl+V or Shift+Insert | Paste |
| Drag with the left mouse, double-click a word | Select text |
| Ctrl+C with a selection, or Ctrl+Shift+C | Copy (Ctrl+C without a selection interrupts) |
| Mouse wheel, Shift+Page Up/Down | Scroll back through earlier output |
| Ctrl+mouse wheel | Bigger or smaller text |
| Right-click | Copy, Paste, Clear, Restart, Stop |
| Ctrl+S, Ctrl+Z, Ctrl+Shift+Z, Ctrl+Space, Ctrl+Q | Still Blender's save, undo, redo, maximize, quit |

When Claude Code exits, press **Enter** in the terminal to start it again. The
header buttons restart, stop, clear and zoom the terminal. The link icon turns
the bridge on and off.

## Settings

**Preferences > Add-ons > Claude Terminal**:

- **Working Folder**: where Claude Code starts. Empty means the `.blend` file's
  folder, or your home folder for an unsaved file.
- **Start Claude Code automatically**: turn off to get a plain PowerShell.
- **Claude Command / Extra Arguments**: e.g. `--continue` to resume the last
  conversation, or `--model opus`.
- **Shell**: empty uses PowerShell 7 if installed, else Windows PowerShell.
- **Bridge Port**: default 9877. The next free port is used if it's taken.

## Use Claude from outside Blender too

The bridge also works with Claude Code in another terminal or the Claude desktop
app. In the **Claude** sidebar tab, click **Copy Setup for Outside Claude**,
paste the command into a terminal, and run it once. It registers a `blender` MCP
server for your user. Blender has to be open with the bridge running when you
use it.

## Safety

- `execute_python` can run any Python inside Blender, the same as Blender's own
  Python console. Save your work before big changes.
- The bridge listens on `127.0.0.1` only and requires a secret token, so other
  computers and web pages can't use it. **New Bridge Token** in the preferences
  makes a new one; run the outside-Blender setup again after that.

## Known limits

- Windows only for now. The code has a macOS/Linux path, but it hasn't been
  tested and the package is built for Windows.
- Typing through an IME (Chinese, Japanese, Korean input) isn't supported.
- When you turn the add-on off, Blender may print `Failed to remove … winpty …`.
  Windows keeps those files in use until Blender closes. It's harmless, and
  turning the add-on back on works.
- Blender 5.x uses a newer Python. Rebuild with
  `.\build.ps1 -Blender "<path to 5.x blender.exe>" -UpdateWheels` first.

## Development

```
claude_terminal/      the add-on (Blender extension)
  __init__.py         registration and timers
  session.py          the shell process and how Claude Code is launched
  pty_backend.py      ConPTY (Windows) / openpty process backends
  vt.py               terminal emulation (pyte plus scrollback, alt screen, ...)
  render.py           drawing the terminal over a Text Editor area
  input_ops.py        keyboard/mouse input via the "Text Generic" keymap
  keys.py             Blender key events -> terminal bytes
  ui.py               operators, header, sidebar panel, menus
  bridge.py           local TCP bridge, runs commands on Blender's main thread
  tools.py            the commands: scene info, object info, Python, screenshot
  mcp_server.py       stdio MCP server Claude Code starts (standard library only)
  wheels/             pyte, wcwidth, pywinpty for Blender's Python 3.11
build.ps1             builds dist/claude_terminal-<version>.zip
install.ps1           installs the latest release into Blender (used by the guides)
docs/                 install guides and their pictures
tests/
  test_vt.py          terminal emulator checks (Blender's Python, no Blender)
  test_mcp_server.py  MCP protocol checks against a running bridge
  dev_cycle.ps1       rebuild + install into a throwaway profile + launch Blender
  boot_test.py        startup script used by dev_cycle.ps1
  bridge_client.py    send one bridge command by hand
```

`tests/dev_cycle.ps1` installs into `%TEMP%\cct\bl_user`, so your normal Blender
settings aren't touched.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)), as Blender add-ons require.

The add-on bundles these Python packages as wheels, each under its own license
(included inside the wheel):

| Package | License |
| --- | --- |
| [pyte](https://github.com/selectel/pyte) | LGPL-3.0 |
| [wcwidth](https://github.com/jquast/wcwidth) | MIT |
| [pywinpty](https://github.com/andfoy/pywinpty) (ships Microsoft's ConPTY / OpenConsole) | MIT |

The escape-sequence parser in `claude_terminal/vt.py` is adapted from pyte.

This is an independent project. It isn't made by or affiliated with Anthropic or
the Blender Foundation.
