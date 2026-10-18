# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard and mouse input for the terminal.

Keys reach the terminal through add-on keymap items in the "Text Generic"
keymap, which Blender checks before the Text Editor's own shortcuts. So the
terminal receives keys while the mouse is over it (Blender's usual
hover-to-focus rule), and no modal operator has to run all the time.
"""

import bpy
from bpy.props import IntProperty

from . import keys, render, session, vt
from .prefs import get_prefs


def _poll(context):
    return session.is_terminal_space(context.space_data)


def _copy_selection(context):
    sess = session.get()
    text = sess.selected_text()
    if text:
        context.window_manager.clipboard = text
    sess.selection = None
    session.tag_redraw()
    return bool(text)


def _is_blender_shortcut(event):
    """Keys that keep their Blender meaning over the terminal: save, undo/redo,
    quit and maximize area. The Text Editor doesn't use them, so passing them
    on reaches Blender's window and screen shortcuts."""
    if not event.ctrl or event.oskey:
        return False
    t = event.type
    if t == 'SPACE':
        return True
    if event.alt:
        return False
    return t in ('S', 'Z') or (t == 'Q' and not event.shift)


class CLAUDETERM_OT_key(bpy.types.Operator):
    bl_idname = "claude_terminal.key"
    bl_label = "Terminal Key"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        sess = session.get()
        t = event.type

        if _is_blender_shortcut(event):
            return {'PASS_THROUGH'}

        if (event.ctrl and t == 'V' and not event.alt) or (event.shift and t == 'INSERT'):
            sess.paste(context.window_manager.clipboard)
            session.tag_redraw()
            return {'FINISHED'}
        if event.ctrl and t == 'C' and not event.alt and (event.shift or sess.selection):
            _copy_selection(context)
            return {'FINISHED'}
        if event.shift and t in ('PAGE_UP', 'PAGE_DOWN'):
            step = max(1, sess.rows - 1)
            sess.scroll_by(step if t == 'PAGE_UP' else -step)
            session.tag_redraw()
            return {'FINISHED'}

        if not sess.running:
            if t in ('RET', 'NUMPAD_ENTER'):
                session.start_terminal()
            return {'FINISHED'}

        app_cursor = vt.DECCKM in sess.screen.mode
        seq = keys.translate(event, app_cursor=app_cursor)
        if seq:
            sess.selection = None
            sess.write(seq)
            session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_scroll(bpy.types.Operator):
    bl_idname = "claude_terminal.scroll"
    bl_label = "Scroll Terminal"
    bl_options = {'INTERNAL'}

    lines: IntProperty(default=3)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        if event.ctrl:
            prefs = get_prefs()
            prefs.font_size = max(7, min(40, prefs.font_size + (1 if self.lines > 0 else -1)))
        else:
            session.get().scroll_by(self.lines)
        session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_select(bpy.types.Operator):
    bl_idname = "claude_terminal.select"
    bl_label = "Select Terminal Text"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and session.get().screen is not None

    def _pos(self, context, event):
        region = context.region
        cell = render.cell_at(region, event.mouse_region_x, event.mouse_region_y)
        if cell is None:
            return None
        row, col = cell
        return (session.get().line_index(row), col)

    def invoke(self, context, event):
        sess = session.get()
        pos = self._pos(context, event)
        if pos is None:
            return {'PASS_THROUGH'}
        if event.shift and sess.selection:
            sess.selection[1] = pos
        else:
            sess.selection = [pos, pos]
        session.tag_redraw()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        sess = session.get()
        if event.type == 'MOUSEMOVE':
            L = render.layout_for(context.region)
            if L is not None:
                # Drag past the top or bottom edge to scroll.
                if event.mouse_region_y > L.height:
                    sess.scroll_by(1)
                elif event.mouse_region_y < 0:
                    sess.scroll_by(-1)
            pos = self._pos(context, event)
            if pos is not None and sess.selection:
                sess.selection[1] = pos
            session.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if sess.selection and sess.selection[0] == sess.selection[1]:
                sess.selection = None
                session.tag_redraw()
            return {'FINISHED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            sess.selection = None
            session.tag_redraw()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class CLAUDETERM_OT_select_word(bpy.types.Operator):
    bl_idname = "claude_terminal.select_word"
    bl_label = "Select Word"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and session.get().screen is not None

    def invoke(self, context, event):
        sess = session.get()
        cell = render.cell_at(context.region, event.mouse_region_x, event.mouse_region_y)
        if cell is None:
            return {'PASS_THROUGH'}
        row, col = cell
        index = sess.line_index(row)
        line = sess.screen.line_by_index(index)
        if line is None:
            return {'FINISHED'}
        text = [line[x].data or " " for x in range(sess.screen.columns)]

        def is_word(c):
            return c.isalnum() or c in "_-./\\:~"

        if col >= len(text) or not is_word(text[col]):
            return {'FINISHED'}
        start = col
        while start > 0 and is_word(text[start - 1]):
            start -= 1
        end = col
        while end + 1 < len(text) and is_word(text[end + 1]):
            end += 1
        sess.selection = [(index, start), (index, end)]
        session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_context_menu(bpy.types.Operator):
    bl_idname = "claude_terminal.context_menu"
    bl_label = "Terminal Menu"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        bpy.ops.wm.call_menu(name="CLAUDETERM_MT_context")
        return {'FINISHED'}


classes = (
    CLAUDETERM_OT_key,
    CLAUDETERM_OT_scroll,
    CLAUDETERM_OT_select,
    CLAUDETERM_OT_select_word,
    CLAUDETERM_OT_context_menu,
)

_keymap_items = []


def register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    km = kc.keymaps.new(name="Text Generic", space_type='TEXT_EDITOR')

    def add(idname, type_, value='PRESS', **kwargs):
        kmi = km.keymap_items.new(idname, type_, value, head=True, **kwargs)
        _keymap_items.append((km, kmi))
        return kmi

    for type_ in keys.KEY_TYPES:
        add("claude_terminal.key", type_, 'ANY' if type_ == 'TEXTINPUT' else 'PRESS',
            any=True, repeat=True)
    add("claude_terminal.scroll", 'WHEELUPMOUSE', any=True).properties.lines = 3
    add("claude_terminal.scroll", 'WHEELDOWNMOUSE', any=True).properties.lines = -3
    add("claude_terminal.select_word", 'LEFTMOUSE', value='DOUBLE_CLICK')
    add("claude_terminal.select", 'LEFTMOUSE', any=True)
    add("claude_terminal.context_menu", 'RIGHTMOUSE')


def unregister_keymaps():
    for km, kmi in _keymap_items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()
