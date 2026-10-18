# SPDX-License-Identifier: GPL-3.0-or-later
"""Terminal emulation on top of pyte.

pyte does the VT100 heavy lifting. This module adds what a modern TUI such as
Claude Code needs and pyte lacks: scrollback history, the alternate screen,
dim text, scroll up/down (SU/SD), repeat (REP), and a parser that ignores
vendor sequences (kitty keyboard, XTVERSION, modifyOtherKeys, DCS/APC strings)
instead of printing them as garbage.
"""

import collections

import pyte
from pyte import control as ctrl
from pyte import modes as mo
from pyte.screens import Margins, StaticDefaultDict

# Private DEC modes are stored shifted by 5 bits in ``Screen.mode``.
DECCKM = 1 << 5            # Application cursor keys.
BRACKETED_PASTE = 2004 << 5
_ALT_SCREEN = (47, 1047, 1049)
_IGNORED_PRIVATE_MODES = (3,)  # DECCOLM would force 132 columns.


class TermScreen(pyte.Screen):
    """A pyte screen with scrollback, alternate screen and a few extra controls.

    ``Char.blink`` is reused to mean "dim" (SGR 2). Real blinking is ignored,
    and nothing in a modern TUI depends on it.
    """

    def __init__(self, columns, lines, history=5000):
        self.history = collections.deque(maxlen=max(100, history))
        # Total number of lines that ever left the top of the screen. Gives
        # every line a stable absolute index for selections.
        self.lines_scrolled = 0
        self.reply = None          # callable(str): answers to terminal queries
        self.alt_active = False
        self._main_buffer = None
        self.last_char = " "
        self.bell_count = 0
        super().__init__(columns, lines)

    # -- plumbing ---------------------------------------------------------

    def write_process_input(self, data):
        if self.reply is not None:
            self.reply(data)

    def bell(self, *args):
        self.bell_count += 1

    def _blank_line(self):
        return StaticDefaultDict(self.default_char)

    def _push_history(self, line):
        if self.alt_active:
            return
        self.history.append(line if line is not None else self._blank_line())
        self.lines_scrolled += 1

    # -- scrolling ----------------------------------------------------------

    def index(self):
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            # The top line is about to leave the screen: keep it.
            self._push_history(self.buffer.get(0))
        super().index()

    def scroll_up(self, count=None, **kwargs):
        """SU: move content up within the margins, cursor stays put."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        for _ in range(min(max(1, count or 1), bottom - top + 1)):
            if top == 0:
                self._push_history(self.buffer.get(0))
            for y in range(top, bottom):
                self.buffer[y] = self.buffer[y + 1]
            self.buffer.pop(bottom, None)
        self.dirty.update(range(self.lines))

    def scroll_down(self, count=None, **kwargs):
        """SD: move content down within the margins, cursor stays put."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        for _ in range(min(max(1, count or 1), bottom - top + 1)):
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
            self.buffer.pop(top, None)
        self.dirty.update(range(self.lines))

    def erase_in_display(self, how=0, *args, **kwargs):
        if how == 3:
            # xterm: ED 3 clears the scrollback only.
            self.history.clear()
            return
        super().erase_in_display(how, *args, **kwargs)

    # -- text ---------------------------------------------------------------

    def draw(self, data):
        if data:
            self.last_char = data[-1]
        super().draw(data)

    def repeat_char(self, count=None, **kwargs):
        """REP: repeat the last printed character."""
        count = max(1, min(count or 1, self.columns * self.lines))
        self.draw(self.last_char * count)

    def select_graphic_rendition(self, *attrs, **kwargs):
        if kwargs.get("private"):
            return
        if not attrs:
            super().select_graphic_rendition()
            return
        out = []
        attrs = list(attrs)
        i = 0
        while i < len(attrs):
            a = attrs[i]
            nxt = attrs[i + 1] if i + 1 < len(attrs) else None
            if a in (38, 48, 58, 59):
                step = 1
                if a != 59:
                    step = 3 if nxt == 5 else 5 if nxt == 2 else 1
                if a in (38, 48) and step > 1:
                    out.extend(attrs[i:i + step])
                i += step   # 58/59 (underline colour) are skipped entirely
                continue
            if a == 2:
                out.append(5)            # dim -> stored in the blink slot
            elif a == 22:
                out.extend((22, 25))     # normal intensity clears bold and dim
            elif a == 21:
                out.append(4)            # double underline -> underline
            elif a in (5, 6):
                pass                     # real blink: not supported
            else:
                out.append(a)
            i += 1
        if out:
            super().select_graphic_rendition(*out)

    # -- modes ----------------------------------------------------------------

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            modes = tuple(m for m in modes if m not in _IGNORED_PRIVATE_MODES)
            if any(m in _ALT_SCREEN for m in modes):
                self._enter_alt(save_cursor=1049 in modes)
        if modes:
            super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            modes = tuple(m for m in modes if m not in _IGNORED_PRIVATE_MODES)
            if any(m in _ALT_SCREEN for m in modes):
                self._exit_alt(restore_cursor=1049 in modes)
        if modes:
            super().reset_mode(*modes, **kwargs)

    def _enter_alt(self, save_cursor):
        if self.alt_active:
            return
        if save_cursor:
            self.save_cursor()
        self._main_buffer = self.buffer
        self.buffer = collections.defaultdict(self._blank_line)
        self.alt_active = True
        self.dirty.update(range(self.lines))

    def _exit_alt(self, restore_cursor):
        if not self.alt_active:
            return
        self.buffer = self._main_buffer
        self._main_buffer = None
        self.alt_active = False
        if restore_cursor:
            self.restore_cursor()
        self.dirty.update(range(self.lines))

    def reset(self):
        if getattr(self, "alt_active", False):
            self._exit_alt(restore_cursor=False)
        super().reset()

    # -- small CSI extras -----------------------------------------------------

    def csi_save_cursor(self, *args, **kwargs):
        if not kwargs.get("private") and not any(args):
            self.save_cursor()

    def csi_restore_cursor(self, *args, **kwargs):
        if not kwargs.get("private") and not any(args):
            self.restore_cursor()

    def report_device_status(self, mode=0, **kwargs):
        if kwargs.get("private"):
            return
        super().report_device_status(mode)

    # -- size -------------------------------------------------------------------

    def resize(self, lines=None, columns=None):
        lines = lines or self.lines
        columns = columns or self.columns
        if lines == self.lines and columns == self.columns:
            return
        if lines < self.lines:
            # Keep the cursor on screen by pushing top lines into history,
            # the way a real terminal does when its window gets shorter.
            excess = self.cursor.y - (lines - 1)
            for _ in range(max(0, excess)):
                self._push_history(self.buffer.get(0))
                for y in range(0, self.lines - 1):
                    self.buffer[y] = self.buffer[y + 1]
                self.buffer.pop(self.lines - 1, None)
            if excess > 0:
                self.cursor.y -= excess
            for y in [y for y in self.buffer if y >= lines]:
                del self.buffer[y]
        # Cells beyond the new width are kept (not drawn) so widening again
        # brings them back.
        self.lines, self.columns = lines, columns
        self.margins = None
        self.tabstops = set(range(8, columns, 8))
        self.cursor.x = min(max(0, self.cursor.x), columns - 1)
        self.cursor.y = min(max(0, self.cursor.y), lines - 1)
        self.dirty.update(range(lines))

    # -- reading ------------------------------------------------------------------

    def view(self, scroll=0):
        """Return ``[(absolute_index, line_or_None), ...]`` for one screenful.

        ``scroll`` is how many lines the view is moved up into the history.
        """
        hist = self.history
        n_hist = len(hist)
        scroll = max(0, min(scroll, n_hist))
        start = n_hist - scroll
        base = self.lines_scrolled - n_hist
        rows = []
        for i in range(start, start + self.lines):
            if i < n_hist:
                rows.append((base + i, hist[i]))
            else:
                rows.append((base + i, self.buffer.get(i - n_hist)))
        return rows

    def line_by_index(self, index):
        """Line for an absolute index, or ``None`` if it is gone or blank."""
        n_hist = len(self.history)
        rel = index - (self.lines_scrolled - n_hist)
        if rel < 0:
            return None
        if rel < n_hist:
            return self.history[rel]
        return self.buffer.get(rel - n_hist)

    def first_index(self):
        return self.lines_scrolled - len(self.history)

    def last_index(self):
        return self.lines_scrolled + self.lines - 1

    def line_text(self, line, start=0, end=None):
        if line is None:
            return ""
        end = self.columns if end is None else min(end, self.columns)
        out = []
        for x in range(max(0, start), end):
            data = line[x].data
            if data:
                out.append(data)
        return "".join(out)


class TermStream(pyte.Stream):
    """pyte stream with a more tolerant CSI parser and string-sequence support."""

    csi = dict(pyte.Stream.csi)
    csi.update({
        "S": "scroll_up",
        "T": "scroll_down",
        "b": "repeat_char",
        "s": "csi_save_cursor",
        "u": "csi_restore_cursor",
    })
    events = pyte.Stream.events | frozenset(csi.values())

    def _parser_fsm(self):
        # Adapted from pyte.Stream._parser_fsm (LGPL-3.0, pyte authors).
        basic = self.basic
        listener = self.listener
        draw = listener.draw
        debug = listener.debug

        ESC, CSI_C1 = ctrl.ESC, ctrl.CSI_C1
        OSC_C1 = ctrl.OSC_C1
        NUL_OR_DEL = ctrl.NUL + ctrl.DEL
        CAN_OR_SUB = ctrl.CAN + ctrl.SUB
        ALLOWED_IN_CSI = "".join([ctrl.BEL, ctrl.BS, ctrl.HT, ctrl.LF,
                                  ctrl.VT, ctrl.FF, ctrl.CR])
        OSC_TERMINATORS = set([ctrl.ST_C0, ctrl.ST_C1, ctrl.BEL])

        def dispatcher(mapping):
            return collections.defaultdict(lambda: debug, dict(
                (event, getattr(listener, attr))
                for event, attr in mapping.items()))

        basic_dispatch = dispatcher(basic)
        sharp_dispatch = dispatcher(self.sharp)
        escape_dispatch = dispatcher(self.escape)
        csi_dispatch = dispatcher(self.csi)

        def call(fn, *args, **kwargs):
            try:
                fn(*args, **kwargs)
            except (TypeError, ValueError, IndexError, KeyError):
                pass  # Malformed or unsupported parameters: drop the sequence.

        while True:
            char = yield True

            if char == ESC:
                char = yield None
                if char == "[":
                    char = CSI_C1
                elif char == "]":
                    char = OSC_C1
                elif char in "PX^_":
                    # DCS / SOS / PM / APC: swallow the string up to ST or BEL.
                    while True:
                        char = yield None
                        if char == ctrl.BEL:
                            break
                        if char == ESC:
                            char = yield None
                            if char == "\\":
                                break
                    continue
                else:
                    if char == "#":
                        call(sharp_dispatch[(yield None)])
                    elif char == "%":
                        self.select_other_charset((yield None))
                    elif char in "()*+":
                        code = yield None
                        if not self.use_utf8:
                            call(listener.define_charset, code, mode=char)
                    elif char == " ":
                        yield None  # ESC SP F/G etc.: ignore
                    else:
                        call(escape_dispatch[char])
                    continue

            if char in basic:
                if (char == ctrl.SI or char == ctrl.SO) and self.use_utf8:
                    continue
                call(basic_dispatch[char])
            elif char == CSI_C1:
                params = []
                current = ""
                sub = None
                private = False
                ignore = False
                intermediate = False
                while True:
                    char = yield None
                    if char == "?":
                        private = True
                    elif char in "<=>":
                        ignore = True  # vendor private: kitty, XTVERSION, ...
                    elif char in ALLOWED_IN_CSI:
                        call(basic_dispatch[char])
                    elif char in CAN_OR_SUB:
                        draw(char)
                        break
                    elif char.isdigit():
                        current += char
                    elif char == ":":
                        if sub is None:
                            sub = []
                        sub.append(int(current or 0))
                        current = ""
                    elif char == ";" or "@" <= char <= "~":
                        if sub is not None:
                            sub.append(int(current or 0))
                            params.extend(_canonical_subparams(sub))
                            sub = None
                        else:
                            params.append(min(int(current or 0), 9999))
                        current = ""
                        if char == ";":
                            continue
                        if not ignore and not intermediate:
                            if private:
                                call(csi_dispatch[char], *params, private=True)
                            else:
                                call(csi_dispatch[char], *params)
                        break
                    elif " " <= char <= "/":
                        intermediate = True  # e.g. DECSCUSR "CSI 2 SP q"
                    else:
                        break  # Not a valid CSI byte: abort the sequence.
            elif char == OSC_C1:
                code = ""
                while True:
                    char = yield None
                    if char == ESC:
                        char += yield None
                    if char in OSC_TERMINATORS or char == ";":
                        break
                    code += char
                param = ""
                if char == ";":
                    while True:
                        char = yield None
                        if char == ESC:
                            char += yield None
                        if char in OSC_TERMINATORS:
                            break
                        param += char
                if code in ("0", "1"):
                    call(listener.set_icon_name, param)
                if code in ("0", "2"):
                    call(listener.set_title, param)
            elif char not in NUL_OR_DEL:
                draw(char)


def _canonical_subparams(group):
    """Turn a colon group (``38:2::r:g:b``) into plain SGR parameters."""
    head = group[0]
    if head in (38, 48, 58) and len(group) >= 2:
        if group[1] == 2 and len(group) >= 5:
            return [head, 2] + [min(v, 255) for v in group[-3:]]
        if group[1] == 5 and len(group) >= 3:
            return [head, 5, min(group[2], 255)]
        return [head]
    if head == 4:
        return [4] if len(group) < 2 or group[1] > 0 else [24]
    return [min(head, 9999)]


def make_terminal(columns, lines, history=5000):
    screen = TermScreen(columns, lines, history=history)
    stream = TermStream(screen)
    return screen, stream
