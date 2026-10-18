"""Blender startup script for tests: start the bridge with a known token.

    blender --python tests/boot_test.py   (with CT_TEST_TOKEN and CT_TEST_INFO set)
"""
import json
import os

from bl_ext.user_default.claude_terminal import bridge, prefs

p = prefs.get_prefs()
p.bridge_token = os.environ["CT_TEST_TOKEN"]
b = bridge.get()
b.stop()
b.start(p.bridge_port, p.bridge_token)
with open(os.environ["CT_TEST_INFO"], "w") as f:
    json.dump({"port": b.port, "running": b.running, "error": b.error}, f)
