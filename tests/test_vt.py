"""Checks for the terminal emulator, run with Blender's Python (no Blender needed).

    python -I tests/test_vt.py <dir with extracted pyte, wcwidth and winpty>
    python -I tests/test_vt.py <deps dir> --live "claude"     # also render a live command
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEPS = sys.argv[1]
for name in os.listdir(DEPS):
    path = os.path.join(DEPS, name)
    if os.path.isdir(path):
        sys.path.insert(0, path)
sys.path.insert(0, os.path.join(HERE, "..", "claude_terminal"))
sys.stdout.reconfigure(encoding="utf-8")

import vt  # noqa: E402


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1


check.failed = 0


def term(cols=40, rows=5):
    replies = []
    screen, stream = vt.make_terminal(cols, rows, history=1000)
    screen.reply = replies.append
    return screen, stream, replies


# Vendor sequences must not print garbage or change attributes.
s, st, _ = term()
st.feed("\x1b[>1u\x1b[<u\x1b[>4;2m\x1b[?u\x1b[>0q\x1b[2 qok")
check("vendor CSI ignored", s.display[0].rstrip() == "ok")
check("no underline from CSI > 4;2 m", not s.buffer[0][0].underscore)

# DCS / APC strings are swallowed.
s, st, _ = term()
st.feed("\x1bP+q544e\x1b\\\x1b_Gf=24;AAAA\x1b\\hi")
check("DCS/APC swallowed", s.display[0].rstrip() == "hi")

# Colon SGR forms.
s, st, _ = term()
st.feed("\x1b[38:2::255:0:0mR\x1b[38;2;0;255;0mG\x1b[58:2::1:2:3mU\x1b[0m")
check("colon truecolor fg", s.buffer[0][0].fg == "ff0000")
check("semicolon truecolor fg", s.buffer[0][1].fg == "00ff00")
check("underline colour skipped", s.buffer[0][2].fg == "00ff00" and not s.buffer[0][2].reverse)

# Dim maps to the blink slot, 22 clears it.
s, st, _ = term()
st.feed("\x1b[2md\x1b[22mn")
check("dim on", s.buffer[0][0].blink)
check("dim off", not s.buffer[0][1].blink)

# Scrollback.
s, st, _ = term(cols=10, rows=3)
st.feed("1\r\n2\r\n3\r\n4\r\n5")
check("history kept", [s.line_text(l).rstrip() for l in s.history] == ["1", "2"])
view = s.view(scroll=2)
check("scrolled view", [s.line_text(l).rstrip() for _, l in view] == ["1", "2", "3"])
st.feed("\x1b[3J")
check("ED 3 clears history", len(s.history) == 0 and s.display[2].rstrip() == "5")

# Alternate screen round trip.
s, st, _ = term(cols=10, rows=3)
st.feed("main\x1b[?1049halt\x1b[?1049l")
check("alt screen restores main", s.display[0].rstrip() == "main")

# SU / SD / REP.
s, st, _ = term(cols=10, rows=3)
st.feed("a\r\nb\r\nc\x1b[1S")
check("SU", [l.rstrip() for l in s.display] == ["b", "c", ""])
st.feed("\x1b[H\x1b[1T")
check("SD", [l.rstrip() for l in s.display] == ["", "b", "c"])
st.feed("\x1b[Hx\x1b[3b")
check("REP", s.display[0].rstrip() == "xxxx")

# Replies to queries.
s, st, replies = term()
st.feed("\x1b[c\x1b[3;5H\x1b[6n\x1b[?6n")
check("DA1 + CPR replies", replies == ["\x1b[?6c", "\x1b[3;5R"])

# Shrinking keeps the cursor line visible.
s, st, _ = term(cols=10, rows=5)
st.feed("1\r\n2\r\n3\r\n4\r\n5")
s.resize(3, 10)
check("shrink keeps bottom", [l.rstrip() for l in s.display] == ["3", "4", "5"])

if "--live" in sys.argv:
    import pty_backend
    cmd = sys.argv[sys.argv.index("--live") + 1]
    cols, rows = 100, 30
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "ANTHROPIC", "MCP_"))}
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    screen, stream = vt.make_terminal(cols, rows)
    p = pty_backend.Pty(["powershell.exe", "-NoLogo", "-NoProfile", "-Command", cmd],
                        os.path.join(HERE, ".."), env, cols, rows)
    screen.reply = p.write
    t0 = time.time()
    while time.time() - t0 < 10:
        data = p.read()
        if data:
            stream.feed(data)
        else:
            time.sleep(0.02)
    p.close()
    print("=== live screen ===")
    for line in screen.display:
        print("|" + line.rstrip())

print("failed:", check.failed)
sys.exit(1 if check.failed else 0)
