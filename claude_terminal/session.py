# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal session: one shell process in a pseudo terminal, shared by
every Text Editor that shows the terminal text block."""

import json
import os
import shlex
import shutil
import sys
import tempfile
import time

import bpy

from . import vt
from .prefs import ensure_token, get_prefs
from .pty_backend import Pty

TEXT_FLAG = "claude_terminal"
TEXT_NAME = "Claude Terminal"

# Set by a Claude Code process that launched Blender; the terminal inside
# Blender is a separate, top-level session.
_NESTED_SESSION_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
)


def is_terminal_space(space):
    if space is None or space.type != 'TEXT_EDITOR':
        return False
    text = space.text
    return text is not None and bool(text.get(TEXT_FLAG))


def iter_terminal_areas():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'TEXT_EDITOR' and is_terminal_space(area.spaces.active):
                yield win, area


def tag_redraw():
    for _win, area in iter_terminal_areas():
        area.tag_redraw()


def ensure_text():
    for text in bpy.data.texts:
        if text.get(TEXT_FLAG):
            return text
    text = bpy.data.texts.new(TEXT_NAME)
    text[TEXT_FLAG] = True
    text.write("This text block shows the Claude Terminal.\n"
               "Enable the Claude Terminal add-on to use it.\n")
    return text


class Session:
    def __init__(self):
        self.screen = None
        self.stream = None
        self.pty = None
        self.cols, self.rows = 100, 30
        self.scroll = 0                # lines scrolled up into history
        self.selection = None          # [anchor, head] as (line_index, col)
        self.status = "idle"           # idle | running | exited | error
        self.message = ""
        self.exit_code = None
        self.cwd = ""
        self.label = ""
        self._pending_size = None
        self._pending_since = 0.0

    @property
    def running(self):
        return self.pty is not None and self.status == "running"

    # -- lifecycle --------------------------------------------------------

    def start(self, argv, cwd, env, scrollback, label):
        self.stop()
        if self._pending_size:
            self.cols, self.rows = self._pending_size
            self._pending_size = None
        self.screen, self.stream = vt.make_terminal(self.cols, self.rows, history=scrollback)
        self.screen.reply = self._reply
        self.scroll = 0
        self.selection = None
        self.exit_code = None
        self.cwd = cwd
        self.label = label
        try:
            self.pty = Pty(argv, cwd, env, self.cols, self.rows)
        except Exception as ex:
            self.pty = None
            self.status = "error"
            self.message = str(ex)
            self.notice("Could not start %s: %s" % (argv[0], ex))
            return False
        self.status = "running"
        self.message = ""
        return True

    def stop(self):
        if self.pty is not None:
            try:
                self.pty.close()
            except Exception:
                pass
            self.pty = None
            if self.status == "running":
                self.status = "exited"
                self.notice("Stopped. Press Enter to start again")

    # -- I/O ----------------------------------------------------------------

    def _reply(self, data):
        if self.pty is not None:
            self.pty.write(data)

    def feed(self, data):
        try:
            self.stream.feed(data)
        except Exception:
            # Never let one bad sequence kill the terminal.
            self.stream._initialize_parser()

    def notice(self, text):
        if self.stream is not None:
            self.feed("\r\n\x1b[0;2m[%s]\x1b[0m\r\n" % text)

    def write(self, text):
        if self.running and text:
            self.scroll = 0
            self.pty.write(text)

    def paste(self, text):
        if not text or not self.running:
            return
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        if vt.BRACKETED_PASTE in self.screen.mode:
            text = "\x1b[200~" + text.replace("\x1b[201~", "") + "\x1b[201~"
        self.write(text)

    def pump(self):
        """Read process output and apply pending resizes. True if anything changed."""
        changed = False
        if self._pending_size and time.monotonic() - self._pending_since > 0.12:
            cols, rows = self._pending_size
            self._pending_size = None
            if (cols, rows) != (self.cols, self.rows):
                self.cols, self.rows = cols, rows
                if self.screen is not None:
                    self.screen.resize(rows, cols)
                if self.pty is not None:
                    self.pty.resize(cols, rows)
                changed = True
        if self.pty is None:
            return changed
        data = self.pty.read()
        if data:
            before = self.screen.lines_scrolled
            self.feed(data)
            if self.scroll:
                # Keep the scrolled-back view still while new lines arrive.
                grown = self.screen.lines_scrolled - before
                self.scroll = min(self.scroll + grown, len(self.screen.history))
            changed = True
        elif not self.pty.isalive():
            self.exit_code = self.pty.exit_status()
            self.pty.close()
            self.pty = None
            self.status = "exited"
            code = "" if self.exit_code in (None, 0) else " with code %s" % self.exit_code
            self.notice("Process exited%s. Press Enter to start again" % code)
            changed = True
        return changed

    def request_size(self, cols, rows):
        if self.screen is None:
            self.cols, self.rows = cols, rows
            return
        if (cols, rows) == (self.cols, self.rows):
            self._pending_size = None
            return
        if self._pending_size != (cols, rows):
            self._pending_size = (cols, rows)
            self._pending_since = time.monotonic()

    # -- view / selection -------------------------------------------------------

    def scroll_by(self, lines):
        if self.screen is None:
            return
        self.scroll = max(0, min(self.scroll + lines, len(self.screen.history)))

    def line_index(self, row):
        """Absolute line index of a visible row."""
        return self.screen.lines_scrolled - self.scroll + row

    def ordered_selection(self):
        if not self.selection:
            return None
        a, b = self.selection
        return (a, b) if a <= b else (b, a)

    def selected_text(self):
        sel = self.ordered_selection()
        if not sel or self.screen is None:
            return ""
        (l0, c0), (l1, c1) = sel
        out = []
        for index in range(max(l0, self.screen.first_index()), l1 + 1):
            line = self.screen.line_by_index(index)
            start = c0 if index == l0 else 0
            end = c1 + 1 if index == l1 else None
            out.append(self.screen.line_text(line, start, end).rstrip())
        return "\n".join(out)

    def clear_scrollback(self):
        if self.screen is not None:
            self.screen.history.clear()
        self.scroll = 0
        self.selection = None


_session = Session()


def get():
    return _session


# -- launching ----------------------------------------------------------------


def default_shell():
    if sys.platform == "win32":
        return (shutil.which("pwsh.exe") or shutil.which("powershell.exe")
                or os.environ.get("COMSPEC", "cmd.exe"))
    return os.environ.get("SHELL") or "/bin/bash"


def working_dir(prefs):
    if prefs.working_dir:
        path = bpy.path.abspath(prefs.working_dir)
        if os.path.isdir(path):
            return os.path.normpath(path)
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return os.path.expanduser("~")


def blender_python():
    exe = sys.executable
    if exe and "python" in os.path.basename(exe).lower() and os.path.exists(exe):
        return exe
    name = "python.exe" if sys.platform == "win32" else "python3"
    for path in (os.path.join(sys.prefix, "bin", name), os.path.join(sys.prefix, name)):
        if os.path.exists(path):
            return path
    return exe


def mcp_server_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")


def write_mcp_config(port, token):
    folder = os.path.join(tempfile.gettempdir(), "blender_claude_terminal")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "mcp_%d.json" % os.getpid())
    config = {
        "mcpServers": {
            "blender": {
                "type": "stdio",
                "command": blender_python(),
                "args": ["-I", mcp_server_script()],
                "env": {
                    "BLENDER_BRIDGE_PORT": str(port),
                    "BLENDER_BRIDGE_TOKEN": token,
                },
            }
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return path


def _shell_argv(shell, command):
    name = os.path.basename(shell).lower()
    if name.startswith(("pwsh", "powershell")):
        argv = [shell, "-NoLogo"]
        if command:
            argv += ["-NoExit", "-Command", command]
        return argv
    if name.startswith("cmd"):
        return [shell, "/K", command] if command else [shell]
    if command:
        return [shell, "-l", "-c", "%s; exec %s -l" % (command, shlex.quote(shell))]
    return [shell, "-l"]


def _quote_arg(shell, value):
    name = os.path.basename(shell).lower()
    if name.startswith(("pwsh", "powershell")):
        return "'" + value.replace("'", "''") + "'"
    if name.startswith("cmd"):
        return '"' + value + '"'
    return shlex.quote(value)


def start_terminal(with_claude=None):
    """Start (or restart) the shell, optionally running Claude Code in it."""
    from . import bridge

    prefs = get_prefs()
    if with_claude is None:
        with_claude = prefs.start_claude
    shell = prefs.shell.strip() or default_shell()
    command = None
    label = os.path.basename(shell)
    if with_claude:
        b = bridge.get()
        if not b.running:
            b.start(prefs.bridge_port, ensure_token(prefs))
        parts = [prefs.claude_command.strip() or "claude"]
        if b.running:
            config = write_mcp_config(b.port, b.token)
            parts += ["--mcp-config", _quote_arg(shell, config)]
        if prefs.claude_args.strip():
            parts.append(prefs.claude_args.strip())
        command = " ".join(parts)
        label = "Claude Code"
        if os.path.basename(shell).lower().startswith(("pwsh", "powershell")):
            command = "& " + command

    env = dict(os.environ)
    for key in _NESTED_SESSION_VARS:
        env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "blender-claude-terminal"

    ok = _session.start(_shell_argv(shell, command), working_dir(prefs), env,
                        prefs.scrollback, label)
    tag_redraw()
    return ok
