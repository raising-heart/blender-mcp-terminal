# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws the terminal on top of a Text Editor region with blf and gpu."""

import functools
import math
import os
import sys
from collections import defaultdict

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import session
from .prefs import get_prefs

DEFAULT_FG = (0.85, 0.85, 0.85, 1.0)
DEFAULT_BG = (0.11, 0.11, 0.115, 1.0)
SELECTION = (0.25, 0.45, 0.85, 0.45)
CURSOR = (0.85, 0.85, 0.85, 1.0)
HINT = (0.55, 0.55, 0.58, 1.0)


def _hex(value):
    return (int(value[0:2], 16) / 255.0, int(value[2:4], 16) / 255.0,
            int(value[4:6], 16) / 255.0, 1.0)


# VS Code's terminal palette: readable on a dark background.
NAMED = {
    "black": _hex("000000"), "red": _hex("cd3131"), "green": _hex("0dbc79"),
    "brown": _hex("e5e510"), "blue": _hex("2472c8"), "magenta": _hex("bc3fbc"),
    "cyan": _hex("11a8cd"), "white": _hex("e5e5e5"),
    "brightblack": _hex("666666"), "brightred": _hex("f14c4c"),
    "brightgreen": _hex("23d18b"), "brightbrown": _hex("f5f543"),
    "brightblue": _hex("3b8eea"), "brightmagenta": _hex("d670d6"),
    "bfightmagenta": _hex("d670d6"),  # sic: typo in pyte's table
    "brightcyan": _hex("29b8db"), "brightwhite": _hex("e5e5e5"),
}
_BRIGHT = {"black", "red", "green", "brown", "blue", "magenta", "cyan", "white"}


@functools.lru_cache(maxsize=4096)
def _color(name, is_fg):
    if name == "default":
        return DEFAULT_FG if is_fg else DEFAULT_BG
    named = NAMED.get(name)
    if named:
        return named
    try:
        return _hex(name)
    except (ValueError, IndexError):
        return DEFAULT_FG if is_fg else DEFAULT_BG


@functools.lru_cache(maxsize=16384)
def _style(fg, bg, bold, dim, reverse):
    """(text colour, background colour or None when it is the default)."""
    if bold and fg in _BRIGHT:
        fg = "bright" + fg
    f = _color(fg, True)
    b = _color(bg, False)
    if reverse:
        f, b = b, f
        draw_bg = b
    else:
        draw_bg = None if bg == "default" else b
    if dim:
        f = (f[0] * 0.55 + b[0] * 0.45, f[1] * 0.55 + b[1] * 0.45,
             f[2] * 0.55 + b[2] * 0.45, 1.0)
    return f, draw_bg


# -- fonts and layout ----------------------------------------------------------

_fonts = {"mono": None, "path": None}


def mono_font():
    fid = _fonts["mono"]
    if fid is None:
        fid = -1
        candidates = [os.path.join(bpy.utils.system_resource('DATAFILES', path="fonts"),
                                   "DejaVuSansMono.woff2")]
        if sys.platform == "win32":
            candidates.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                           "Fonts", "consola.ttf"))
        for path in candidates:
            if os.path.exists(path):
                fid = blf.load(path)
                if fid != -1:
                    _fonts["path"] = path
                    break
        if fid == -1:
            fid = 0
        _fonts["mono"] = fid
    return fid


def unload_fonts():
    if _fonts["path"]:
        try:
            blf.unload(_fonts["path"])
        except Exception:
            pass
    _fonts["mono"] = None
    _fonts["path"] = None


class Layout:
    __slots__ = ("size", "cw", "lh", "pad", "base", "width", "height", "cols", "rows", "bold_dx")


@functools.lru_cache(maxsize=64)
def _metrics(fid, size):
    blf.size(fid, size)
    cw = blf.dimensions(fid, "M" * 64)[0] / 64.0
    cap = blf.dimensions(fid, "M")[1]
    lh = math.ceil(size * 1.3)
    base = round((lh - cap) / 2.0)
    return cw, lh, base


def compute_layout(width, height):
    prefs = get_prefs()
    scale = bpy.context.preferences.system.ui_scale
    size = max(6, round((prefs.font_size if prefs else 12) * scale))
    cw, lh, base = _metrics(mono_font(), size)
    L = Layout()
    L.size, L.cw, L.lh, L.base = size, cw, lh, base
    L.pad = round(6 * scale)
    L.width, L.height = width, height
    L.cols = max(10, int((width - 2 * L.pad) // cw))
    L.rows = max(3, int((height - 2 * L.pad) // lh))
    L.bold_dx = max(1, round(size / 16))
    return L


_layouts = {}  # region pointer -> Layout used for the last draw


def cell_at(region, x, y, clamp=True):
    """(row, col) of a region-relative mouse position."""
    L = _layouts.get(region.as_pointer())
    if L is None:
        return None
    col = int((x - L.pad) // L.cw)
    row = int((L.height - L.pad - y) // L.lh)
    if clamp:
        col = max(0, min(col, L.cols - 1))
        row = max(0, min(row, L.rows - 1))
    return row, col


def layout_for(region):
    return _layouts.get(region.as_pointer())


# -- drawing -------------------------------------------------------------------

_shader = None


def _uniform_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _add_rect(verts, x0, y0, x1, y1):
    verts.extend(((x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)))


def _flush_rects(groups):
    shader = _uniform_shader()
    for color, verts in groups.items():
        if verts:
            batch = batch_for_shader(shader, 'TRIS', {"pos": verts})
            shader.uniform_float("color", color)
            batch.draw(shader)


def _is_mono_glyph(ch):
    # The monospace font covers Latin text, punctuation and box drawing.
    # Symbols (arrows, technical, geometric, dingbats, emoji, CJK) go to
    # Blender's UI font, which falls back across its whole font stack.
    o = ord(ch[0])
    return o < 0x2190 or 0x2500 <= o <= 0x259F


def _draw_center_text(L, lines):
    fid = mono_font()
    blf.size(fid, L.size)
    y = L.height / 2 + len(lines) * L.lh / 2
    for text, color in lines:
        y -= L.lh * 1.4
        w = blf.dimensions(fid, text)[0]
        blf.position(fid, max(L.pad, (L.width - w) / 2), y, 0)
        blf.color(fid, *color)
        blf.draw(fid, text)


def draw_terminal():
    context = bpy.context
    if not session.is_terminal_space(context.space_data):
        return
    region = context.region
    if region is None or region.type != 'WINDOW':
        return

    sess = session.get()
    L = compute_layout(region.width, region.height)
    _layouts[region.as_pointer()] = L
    sess.request_size(L.cols, L.rows)

    gpu.state.blend_set('ALPHA')
    _flush_rects({DEFAULT_BG: [(0, 0), (L.width, 0), (L.width, L.height),
                               (0, 0), (L.width, L.height), (0, L.height)]})

    screen = sess.screen
    if screen is None:
        _draw_center_text(L, [
            ("Claude Terminal", DEFAULT_FG),
            ("Move the mouse here and press Enter to start Claude Code", HINT),
        ])
        gpu.state.blend_set('NONE')
        return

    fid = mono_font()
    ui_fid = 0
    blf.size(fid, L.size)
    blf.size(ui_fid, L.size)
    cw, lh, pad = L.cw, L.lh, L.pad
    top = L.height - pad

    view = screen.view(sess.scroll)
    rows = min(len(view), L.rows)
    cols = min(screen.columns, L.cols)

    cur = screen.cursor
    show_cursor = (not cur.hidden) and sess.scroll == 0 and sess.running
    cur_row, cur_col = cur.y, min(cur.x, cols - 1)

    bg_rects = defaultdict(list)
    line_rects = defaultdict(list)    # underline / strikethrough
    runs = []                         # (x, y, text, color, bold)
    singles = []                      # (x, y, char, color, cells)

    for r in range(rows):
        _index, line = view[r]
        if line is None:
            continue
        y_bot = top - (r + 1) * lh
        y_base = y_bot + L.base
        bg_start, bg_color = 0, None
        run_start, run_chars, run_key = 0, [], None

        for x in range(cols + 1):
            if x < cols:
                ch = line[x]
                data = ch.data
                reverse = ch.reverse != (show_cursor and r == cur_row and x == cur_col)
                fgc, bgc = _style(ch.fg, ch.bg, ch.bold, ch.blink, reverse)
            else:
                data, fgc, bgc = None, None, None  # sentinel flushes the runs

            if bgc != bg_color:
                if bg_color is not None:
                    _add_rect(bg_rects[bg_color], pad + bg_start * cw, y_bot,
                              pad + x * cw, y_bot + lh)
                bg_start, bg_color = x, bgc

            key = None
            if data and data != " " and _is_mono_glyph(data):
                key = (fgc, ch.bold, ch.underscore, ch.strikethrough)
            if key != run_key or (key is not None and run_start + len(run_chars) != x):
                if run_chars:
                    runs.append((pad + run_start * cw, y_base, "".join(run_chars),
                                 run_key[0], run_key[1]))
                    x0, x1 = pad + run_start * cw, pad + (run_start + len(run_chars)) * cw
                    if run_key[2]:
                        _add_rect(line_rects[run_key[0]], x0, y_bot + 1, x1, y_bot + 2)
                    if run_key[3]:
                        mid = y_bot + lh // 2
                        _add_rect(line_rects[run_key[0]], x0, mid, x1, mid + 1)
                run_start, run_chars, run_key = x, [], key
            if key is not None:
                run_chars.append(data)
            elif data and data != " ":
                wide = x + 1 < cols and line[x + 1].data == ""
                singles.append((pad + x * cw, y_base, data, fgc, 2 if wide else 1))

    sel = sess.ordered_selection()
    sel_rects = []
    if sel:
        (l0, c0), (l1, c1) = sel
        for r in range(rows):
            index = view[r][0]
            if l0 <= index <= l1:
                c_start = c0 if index == l0 else 0
                c_end = c1 + 1 if index == l1 else cols
                y_bot = top - (r + 1) * lh
                _add_rect(sel_rects, pad + c_start * cw, y_bot, pad + c_end * cw, y_bot + lh)

    if show_cursor and view[cur_row][1] is None and cur_row < rows:
        # Cursor on a blank line that has no cells yet.
        y_bot = top - (cur_row + 1) * lh
        _add_rect(bg_rects[CURSOR], pad + cur_col * cw, y_bot, pad + (cur_col + 1) * cw, y_bot + lh)

    _flush_rects(bg_rects)
    if sel_rects:
        _flush_rects({SELECTION: sel_rects})
    _flush_rects(line_rects)

    for x, y, text, color, bold in runs:
        blf.color(fid, *color)
        blf.position(fid, x, y, 0)
        blf.draw(fid, text)
        if bold:
            blf.position(fid, x + L.bold_dx, y, 0)
            blf.draw(fid, text)

    for x, y, ch, color, cells in singles:
        w = blf.dimensions(ui_fid, ch)[0]
        blf.color(ui_fid, *color)
        blf.position(ui_fid, x + max(0.0, (cells * cw - w) / 2), y, 0)
        blf.draw(ui_fid, ch)

    if sess.scroll:
        label = "\u2191 %d lines up \u2013 scroll down or type to return" % sess.scroll
        w = blf.dimensions(fid, label)[0]
        if w > L.width - pad * 6:
            label = "\u2191 %d" % sess.scroll
            w = blf.dimensions(fid, label)[0]
        x0 = L.width - w - pad * 3
        y0 = L.height - lh - pad * 2
        _flush_rects({(0.2, 0.35, 0.6, 0.9): [(x0 - pad, y0 - pad / 2), (L.width - pad, y0 - pad / 2),
                                               (L.width - pad, y0 + lh), (x0 - pad, y0 - pad / 2),
                                               (L.width - pad, y0 + lh), (x0 - pad, y0 + lh)]})
        blf.color(fid, 1, 1, 1, 1)
        blf.position(fid, x0, y0 + L.base / 2, 0)
        blf.draw(fid, label)

    gpu.state.blend_set('NONE')


_handle = None


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceTextEditor.draw_handler_add(draw_terminal, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceTextEditor.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    _layouts.clear()
    _metrics.cache_clear()
    unload_fonts()
