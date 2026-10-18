# SPDX-License-Identifier: GPL-3.0-or-later
import secrets

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

PACKAGE = __package__


def get_prefs():
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


def ensure_token(prefs):
    if not prefs.bridge_token:
        prefs.bridge_token = secrets.token_urlsafe(24)
    return prefs.bridge_token


def _redraw_terminals(self, context):
    from . import session
    session.tag_redraw()


class ClaudeTerminalPreferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    # Terminal
    font_size: IntProperty(
        name="Font Size", default=12, min=7, max=40,
        description="Terminal font size (Ctrl + mouse wheel over the terminal also changes it)",
        update=_redraw_terminals,
    )
    scrollback: IntProperty(
        name="Scrollback Lines", default=5000, min=200, max=100000,
        description="How many lines the terminal keeps above the screen",
    )
    shell: StringProperty(
        name="Shell",
        description="Shell program to run. Empty: PowerShell on Windows, $SHELL elsewhere",
        default="",
    )
    start_claude: BoolProperty(
        name="Start Claude Code automatically", default=True,
        description="Run Claude Code (connected to this Blender) when the terminal starts",
    )
    claude_command: StringProperty(
        name="Claude Command", default="claude",
        description="Command that starts Claude Code",
    )
    claude_args: StringProperty(
        name="Extra Arguments", default="",
        description="Extra arguments for Claude Code, for example --continue or --model opus",
    )
    working_dir: StringProperty(
        name="Working Folder", subtype='DIR_PATH', default="",
        description="Folder Claude Code starts in. Empty: the .blend file's folder, or your home folder for unsaved files",
    )

    # Bridge
    bridge_autostart: BoolProperty(
        name="Start bridge with Blender", default=True,
        description="Start the local MCP bridge when Blender starts, so Claude outside Blender can connect too",
    )
    bridge_port: IntProperty(
        name="Bridge Port", default=9877, min=1024, max=65535,
        description="Local TCP port for the bridge (the next free port is used if this one is taken)",
    )
    bridge_token: StringProperty(
        name="Bridge Token", default="", options={'HIDDEN'},
        description="Secret that MCP clients must send. Generated automatically",
    )

    def draw(self, context):
        from . import bridge

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Terminal")
        col.prop(self, "font_size")
        col.prop(self, "scrollback")
        col.prop(self, "shell")
        col.prop(self, "working_dir")

        col = layout.column(heading="Claude Code")
        col.prop(self, "start_claude")
        sub = col.column()
        sub.active = self.start_claude
        sub.prop(self, "claude_command")
        sub.prop(self, "claude_args")

        col = layout.column(heading="MCP Bridge")
        col.prop(self, "bridge_autostart")
        col.prop(self, "bridge_port")
        row = col.row()
        b = bridge.get()
        if b.running:
            row.label(text="Running on 127.0.0.1:%d" % b.port, icon='LINKED')
        else:
            row.label(text=b.error or "Stopped", icon='UNLINKED')

        col = layout.column(heading="Claude outside Blender")
        col.operator("claude_terminal.copy_setup_command", icon='COPYDOWN')
        col.operator("claude_terminal.regenerate_token", icon='FILE_REFRESH')


classes = (ClaudeTerminalPreferences,)
