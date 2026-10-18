# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, IntProperty

from . import bridge, session
from .prefs import ensure_token, get_prefs


def _largest_area(screen, area_type):
    best = None
    for area in screen.areas:
        if area.type == area_type and (best is None or area.width * area.height > best.width * best.height):
            best = area
    return best


def _window_region(area):
    for region in area.regions:
        if region.type == 'WINDOW':
            return region
    return None


def _show_terminal_in(area, text):
    area.type = 'TEXT_EDITOR'
    area.show_menus = False  # collapse the Text Editor menus to make room for ours
    space = area.spaces.active
    space.text = text
    space.show_line_numbers = False
    space.show_syntax_highlight = False
    space.show_word_wrap = False
    space.show_margin = False
    space.show_region_footer = False


class CLAUDETERM_OT_open(bpy.types.Operator):
    bl_idname = "claude_terminal.open"
    bl_label = "Open Claude Terminal"
    bl_description = "Show the terminal next to the 3D Viewport and start Claude Code"

    def execute(self, context):
        text = session.ensure_text()
        screen = context.screen
        target = None
        for area in screen.areas:
            if area.type == 'TEXT_EDITOR' and session.is_terminal_space(area.spaces.active):
                target = area
                break

        if target is None:
            view3d = _largest_area(screen, 'VIEW_3D')
            if view3d is not None:
                before = {a.as_pointer() for a in screen.areas}
                with context.temp_override(area=view3d, region=_window_region(view3d)):
                    bpy.ops.screen.area_split(direction='VERTICAL', factor=0.58)
                # The original area keeps the left part; the new one is on the
                # right (its size isn't laid out until the next redraw).
                new = [a for a in screen.areas if a.as_pointer() not in before]
                target = new[0] if new else view3d
            elif context.area is not None:
                target = context.area
            else:
                self.report({'ERROR'}, "No area to show the terminal in")
                return {'CANCELLED'}
            _show_terminal_in(target, text)

        sess = session.get()
        if not sess.running:
            session.start_terminal()
        target.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_restart(bpy.types.Operator):
    bl_idname = "claude_terminal.restart"
    bl_label = "Restart Terminal"
    bl_description = "Stop the terminal process and start it again"

    shell_only: BoolProperty(
        name="Shell Only", default=False,
        description="Start a plain shell without Claude Code",
    )

    def execute(self, context):
        session.start_terminal(with_claude=False if self.shell_only else None)
        return {'FINISHED'}


class CLAUDETERM_OT_stop(bpy.types.Operator):
    bl_idname = "claude_terminal.stop"
    bl_label = "Stop Terminal"
    bl_description = "End the terminal process (Claude Code and the shell)"

    @classmethod
    def poll(cls, context):
        return session.get().running

    def execute(self, context):
        session.get().stop()
        session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_clear(bpy.types.Operator):
    bl_idname = "claude_terminal.clear"
    bl_label = "Clear Scrollback"
    bl_description = "Forget the lines above the screen"

    def execute(self, context):
        session.get().clear_scrollback()
        session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_copy(bpy.types.Operator):
    bl_idname = "claude_terminal.copy"
    bl_label = "Copy"
    bl_description = "Copy the selected terminal text"

    @classmethod
    def poll(cls, context):
        return bool(session.get().selection)

    def execute(self, context):
        sess = session.get()
        context.window_manager.clipboard = sess.selected_text()
        sess.selection = None
        session.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_paste(bpy.types.Operator):
    bl_idname = "claude_terminal.paste"
    bl_label = "Paste"
    bl_description = "Paste the clipboard into the terminal"

    @classmethod
    def poll(cls, context):
        return session.get().running

    def execute(self, context):
        session.get().paste(context.window_manager.clipboard)
        return {'FINISHED'}


class CLAUDETERM_OT_font_size(bpy.types.Operator):
    bl_idname = "claude_terminal.font_size"
    bl_label = "Terminal Font Size"
    bl_description = "Make the terminal text bigger or smaller"

    delta: IntProperty(default=1)

    def execute(self, context):
        prefs = get_prefs()
        prefs.font_size = max(7, min(40, prefs.font_size + self.delta))
        return {'FINISHED'}


class CLAUDETERM_OT_bridge_toggle(bpy.types.Operator):
    bl_idname = "claude_terminal.bridge_toggle"
    bl_label = "Toggle MCP Bridge"
    bl_description = "Start or stop the local bridge that lets Claude Code control Blender"

    def execute(self, context):
        b = bridge.get()
        if b.running:
            b.stop()
        else:
            prefs = get_prefs()
            if not b.start(prefs.bridge_port, ensure_token(prefs)):
                self.report({'ERROR'}, b.error)
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class CLAUDETERM_OT_copy_setup_command(bpy.types.Operator):
    bl_idname = "claude_terminal.copy_setup_command"
    bl_label = "Copy Setup Command"
    bl_description = ("Copy a 'claude mcp add' command that connects Claude Code running "
                      "outside Blender (another terminal or the desktop app) to this bridge")

    def execute(self, context):
        prefs = get_prefs()
        token = ensure_token(prefs)
        b = bridge.get()
        port = b.port if b.running else prefs.bridge_port
        cmd = ('claude mcp add blender --scope user '
               '-e BLENDER_BRIDGE_PORT=%d -e BLENDER_BRIDGE_TOKEN=%s -- "%s" -I "%s"'
               % (port, token, session.blender_python(), session.mcp_server_script()))
        context.window_manager.clipboard = cmd
        self.report({'INFO'}, "Copied. Paste it into a terminal and run it once")
        return {'FINISHED'}


class CLAUDETERM_OT_regenerate_token(bpy.types.Operator):
    bl_idname = "claude_terminal.regenerate_token"
    bl_label = "New Bridge Token"
    bl_description = ("Make a new secret. Clients set up with the old one stop working "
                      "until you run the setup command again")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = get_prefs()
        prefs.bridge_token = ""
        token = ensure_token(prefs)
        b = bridge.get()
        if b.running:
            b.token = token
        return {'FINISHED'}


class CLAUDETERM_MT_context(bpy.types.Menu):
    bl_idname = "CLAUDETERM_MT_context"
    bl_label = "Terminal"

    def draw(self, context):
        layout = self.layout
        layout.operator("claude_terminal.copy", icon='COPYDOWN')
        layout.operator("claude_terminal.paste", icon='PASTEDOWN')
        layout.separator()
        layout.operator("claude_terminal.clear", icon='TRASH')
        layout.separator()
        layout.operator("claude_terminal.restart", text="Restart Claude Code", icon='FILE_REFRESH')
        layout.operator("claude_terminal.restart", text="Restart as Plain Shell",
                        icon='CONSOLE').shell_only = True
        layout.operator("claude_terminal.stop", icon='CANCEL')


def _status_text():
    sess = session.get()
    if sess.status == "running":
        return sess.label
    if sess.status == "error":
        return "Error: " + sess.message
    if sess.status == "exited":
        return "Stopped"
    return "Not started"


def draw_text_header(self, context):
    # Prepended to the Text Editor header so the controls stay visible even
    # when the area is narrow.
    if not session.is_terminal_space(context.space_data):
        return
    layout = self.layout
    row = layout.row(align=True)
    row.label(text=_status_text(), icon='CONSOLE')
    row = layout.row(align=True)
    row.operator("claude_terminal.restart", text="", icon='FILE_REFRESH')
    row.operator("claude_terminal.stop", text="", icon='CANCEL')
    row.operator("claude_terminal.clear", text="", icon='TRASH')
    row = layout.row(align=True)
    row.operator("claude_terminal.font_size", text="", icon='ZOOM_OUT').delta = -1
    row.operator("claude_terminal.font_size", text="", icon='ZOOM_IN').delta = 1
    b = bridge.get()
    layout.operator("claude_terminal.bridge_toggle", text="",
                    icon='LINKED' if b.running else 'UNLINKED', depress=b.running)
    layout.separator()


class CLAUDETERM_PT_sidebar(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Claude"
    bl_label = "Claude Terminal"

    def draw(self, context):
        layout = self.layout
        sess = session.get()
        b = bridge.get()

        layout.operator("claude_terminal.open", icon='CONSOLE')

        box = layout.box()
        col = box.column(align=True)
        col.label(text="Terminal: " + _status_text())
        if sess.cwd:
            col.label(text=sess.cwd, icon='FILE_FOLDER')
        row = box.row(align=True)
        row.operator("claude_terminal.restart", text="Restart", icon='FILE_REFRESH')
        row.operator("claude_terminal.stop", text="Stop", icon='CANCEL')

        box = layout.box()
        row = box.row()
        if b.running:
            row.label(text="Bridge on port %d" % b.port, icon='LINKED')
        else:
            row.label(text=b.error or "Bridge stopped", icon='UNLINKED')
        row.operator("claude_terminal.bridge_toggle", text="Stop" if b.running else "Start")
        if b.last_command:
            box.label(text="Last: " + b.last_command, icon='TIME')
        box.operator("claude_terminal.copy_setup_command", text="Copy Setup for Outside Claude",
                     icon='COPYDOWN')


def draw_window_menu(self, context):
    self.layout.separator()
    self.layout.operator("claude_terminal.open", icon='CONSOLE')


classes = (
    CLAUDETERM_OT_open,
    CLAUDETERM_OT_restart,
    CLAUDETERM_OT_stop,
    CLAUDETERM_OT_clear,
    CLAUDETERM_OT_copy,
    CLAUDETERM_OT_paste,
    CLAUDETERM_OT_font_size,
    CLAUDETERM_OT_bridge_toggle,
    CLAUDETERM_OT_copy_setup_command,
    CLAUDETERM_OT_regenerate_token,
    CLAUDETERM_MT_context,
    CLAUDETERM_PT_sidebar,
)


def register_menus():
    bpy.types.TEXT_HT_header.prepend(draw_text_header)
    bpy.types.TOPBAR_MT_window.append(draw_window_menu)


def unregister_menus():
    bpy.types.TEXT_HT_header.remove(draw_text_header)
    bpy.types.TOPBAR_MT_window.remove(draw_window_menu)
