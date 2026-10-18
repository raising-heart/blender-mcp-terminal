# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate Blender key events into the bytes a terminal would send (xterm style)."""

LETTERS = tuple(chr(c) for c in range(ord("A"), ord("Z") + 1))
DIGITS = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")
NUMPAD = tuple("NUMPAD_%d" % i for i in range(10)) + (
    "NUMPAD_PERIOD", "NUMPAD_SLASH", "NUMPAD_ASTERIX", "NUMPAD_MINUS", "NUMPAD_PLUS",
    "NUMPAD_ENTER",
)
SYMBOLS = (
    "ACCENT_GRAVE", "MINUS", "EQUAL", "LEFT_BRACKET", "RIGHT_BRACKET", "BACK_SLASH",
    "SEMI_COLON", "QUOTE", "COMMA", "PERIOD", "SLASH", "SPACE",
)
SPECIAL = (
    "RET", "TAB", "BACK_SPACE", "DEL", "INSERT", "ESC", "HOME", "END",
    "PAGE_UP", "PAGE_DOWN", "UP_ARROW", "DOWN_ARROW", "LEFT_ARROW", "RIGHT_ARROW",
)

# Every key type the terminal listens to. Function keys are left to Blender.
KEY_TYPES = LETTERS + DIGITS + NUMPAD + SYMBOLS + SPECIAL + ("TEXTINPUT",)

_ARROWS = {"UP_ARROW": "A", "DOWN_ARROW": "B", "RIGHT_ARROW": "C", "LEFT_ARROW": "D"}
_HOME_END = {"HOME": "H", "END": "F"}
_TILDE = {"INSERT": 2, "DEL": 3, "PAGE_UP": 5, "PAGE_DOWN": 6}

# Ctrl + punctuation -> C0 control codes.
_CTRL_SYMBOLS = {
    "SPACE": "\x00", "TWO": "\x00",
    "LEFT_BRACKET": "\x1b", "THREE": "\x1b",
    "BACK_SLASH": "\x1c", "FOUR": "\x1c",
    "RIGHT_BRACKET": "\x1d", "FIVE": "\x1d",
    "SIX": "\x1e",
    "MINUS": "\x1f", "SEVEN": "\x1f", "SLASH": "\x1f",
    "EIGHT": "\x7f",
}

# Plain characters for keys when Blender gives no unicode (Alt combos).
_PLAIN = {
    "ACCENT_GRAVE": ("`", "~"), "MINUS": ("-", "_"), "EQUAL": ("=", "+"),
    "LEFT_BRACKET": ("[", "{"), "RIGHT_BRACKET": ("]", "}"), "BACK_SLASH": ("\\", "|"),
    "SEMI_COLON": (";", ":"), "QUOTE": ("'", '"'), "COMMA": (",", "<"),
    "PERIOD": (".", ">"), "SLASH": ("/", "?"), "SPACE": (" ", " "),
}
for _i, _name in enumerate(DIGITS):
    _PLAIN[_name] = (str(_i), ")!@#$%^&*("[_i])


def _modifier_param(shift, alt, ctrl):
    return 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)


def _plain_char(event_type, shift):
    if event_type in LETTERS:
        return event_type if shift else event_type.lower()
    pair = _PLAIN.get(event_type)
    return pair[1 if shift else 0] if pair else ""


def translate(event, app_cursor=False):
    """Return the string to send for a key event, or None to ignore it."""
    t = event.type
    shift, ctrl, alt = event.shift, event.ctrl, event.alt
    mod = _modifier_param(shift, alt, ctrl)

    if t in _ARROWS:
        if mod > 1:
            return "\x1b[1;%d%s" % (mod, _ARROWS[t])
        return ("\x1bO" if app_cursor else "\x1b[") + _ARROWS[t]
    if t in _HOME_END:
        if mod > 1:
            return "\x1b[1;%d%s" % (mod, _HOME_END[t])
        return ("\x1bO" if app_cursor else "\x1b[") + _HOME_END[t]
    if t in _TILDE:
        if mod > 1:
            return "\x1b[%d;%d~" % (_TILDE[t], mod)
        return "\x1b[%d~" % _TILDE[t]
    if t == "TAB":
        return "\x1b[Z" if shift else "\t"
    if t in ("RET", "NUMPAD_ENTER"):
        # Shift/Alt+Enter: ESC CR, which Claude Code reads as "new line".
        return "\x1b\r" if (shift or alt) else "\r"
    if t == "BACK_SPACE":
        if ctrl:
            return "\x08"          # same as Windows Terminal: delete previous word
        return "\x1b\x7f" if alt else "\x7f"
    if t == "ESC":
        return "\x1b"

    uni = event.unicode
    if ctrl and alt and uni and ord(uni[0]) >= 32:
        return uni                 # AltGr on European layouts arrives as Ctrl+Alt
    if ctrl:
        if t in LETTERS:
            code = chr(ord(t) - 64)
            return "\x1b" + code if alt else code
        code = _CTRL_SYMBOLS.get(t)
        if code is not None:
            return "\x1b" + code if alt else code
        return None
    if alt:
        ch = uni or _plain_char(t, shift)
        return "\x1b" + ch if ch else None
    if uni:
        return uni
    return None
