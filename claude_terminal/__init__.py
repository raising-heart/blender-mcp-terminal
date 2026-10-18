# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude Terminal: a terminal inside Blender running Claude Code, plus a
local MCP bridge so Claude can build and inspect the open scene live."""

import atexit
import time

import bpy

from . import bridge, input_ops, prefs, render, session, ui

_classes = prefs.classes + input_ops.classes + ui.classes

_FAST, _IDLE = 1 / 60, 0.05
_last_activity = 0.0


def _pump():
    """Main-thread timer: move terminal output onto the screen."""
    global _last_activity
    sess = session.get()
    try:
        changed = sess.pump()
    except Exception as ex:
        print("Claude Terminal:", ex)
        changed = False
    if changed:
        _last_activity = time.monotonic()
        session.tag_redraw()
    if sess.pty is None and not sess._pending_size:
        return 0.25
    return _FAST if time.monotonic() - _last_activity < 0.5 else _IDLE


def _autostart_bridge():
    p = prefs.get_prefs()
    if p is not None and p.bridge_autostart and not bridge.get().running:
        if not bridge.get().start(p.bridge_port, prefs.ensure_token(p)):
            print("Claude Terminal: bridge not started:", bridge.get().error)
    return None


def _shutdown():
    session.get().stop()
    bridge.get().stop()


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    render.register()
    input_ops.register_keymaps()
    ui.register_menus()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1, persistent=True)
    if not bpy.app.background:
        # persistent: Blender loads the startup file (or the opened .blend)
        # after add-ons register, which cancels non-persistent timers.
        bpy.app.timers.register(_autostart_bridge, first_interval=0.5, persistent=True)
    atexit.register(_shutdown)


def unregister():
    atexit.unregister(_shutdown)
    _shutdown()
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    if bpy.app.timers.is_registered(_autostart_bridge):
        bpy.app.timers.unregister(_autostart_bridge)
    ui.unregister_menus()
    input_ops.unregister_keymaps()
    render.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
