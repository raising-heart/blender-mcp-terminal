# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP server (stdio) that gives Claude Code tools to control a running Blender.

Claude Code starts this script; it forwards tool calls over a local socket to
the Claude Terminal add-on inside Blender. Standard library only, so Blender's
bundled Python can run it:

    <blender python> -I mcp_server.py

Environment:
    BLENDER_BRIDGE_PORT   bridge port (default 9877)
    BLENDER_BRIDGE_TOKEN  bridge secret (Blender's add-on preferences)
    BLENDER_BRIDGE_HOST   default 127.0.0.1
"""

import json
import os
import socket
import sys

SERVER_NAME = "blender"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

HOST = os.environ.get("BLENDER_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BLENDER_BRIDGE_PORT", "9877"))
TOKEN = os.environ.get("BLENDER_BRIDGE_TOKEN", "")

INSTRUCTIONS = """\
These tools control the user's running Blender session. The user watches the
viewport while you work, and every change appears immediately.

How to work well:
- Build in small steps: one execute_python call per logical part (body, head,
  materials...). The user sees progress, and each call is one undo step.
- After visible changes, call get_viewport_screenshot to check the result, and
  fix what looks wrong. Use view="FRONT"/"RIGHT"/"TOP" with frame="ALL" to
  compare against reference sheets.
- Start with get_scene_info to see what already exists. Don't delete or
  overwrite the user's objects unless asked.
- Blender units are meters, Z is up, and the FRONT view looks along +Y (the
  model's front faces -Y).
- Prefer the data API and bmesh over bpy.ops where practical; operators run
  with a 3D Viewport context, so common ones (primitive_add, modifiers,
  mode_set) work too.
- Name objects, meshes and materials clearly and group parts in collections.
"""

TOOLS = [
    {
        "name": "get_scene_info",
        "description": "Summary of the open Blender file: objects with transforms, "
                       "dimensions and materials, the active and selected objects, "
                       "the current mode, collections and units.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum objects to list (default 100)."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_object_info",
        "description": "Details for one object: world bounding box, modifiers, "
                       "constraints, materials (base color, roughness), and mesh stats "
                       "(vertices, faces, UVs, vertex groups, shape keys).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Object name."}},
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "execute_python",
        "description": "Run Python inside Blender on its main thread. bpy, bmesh, "
                       "mathutils, math, Vector, Matrix, Euler and Quaternion are "
                       "preloaded, and variables persist between calls. print() output "
                       "is returned; assign a value to `result` to return it as JSON. "
                       "Each call becomes one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to run."},
                "reset_namespace": {"type": "boolean",
                                    "description": "Forget variables from earlier calls first."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_viewport_screenshot",
        "description": "Capture the 3D Viewport as an image so you can see the result. "
                       "Optionally switch to a fixed view and frame the scene first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_size": {"type": "integer",
                             "description": "Longest side in pixels (default 1024, max 2048)."},
                "view": {"type": "string",
                         "enum": ["CURRENT", "FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "CAMERA"],
                         "description": "View to switch to first (default CURRENT)."},
                "frame": {"type": "string", "enum": ["NONE", "ALL", "SELECTED"],
                          "description": "Zoom to fit everything or the selection first."},
            },
        },
    },
]

_TIMEOUTS = {"execute_python": 600, "get_viewport_screenshot": 120}


class BridgeError(Exception):
    pass


def call_blender(cmd, params):
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError:
        raise BridgeError(
            "Can't reach Blender on %s:%d. Make sure Blender is open and the Claude "
            "Terminal bridge is running (sidebar > Claude in the 3D Viewport)." % (HOST, PORT))
    sock.settimeout(_TIMEOUTS.get(cmd, 60))
    try:
        payload = {"token": TOKEN, "cmd": cmd, "params": params}
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        with sock.makefile("rb") as rfile:
            line = rfile.readline()
    except socket.timeout:
        raise BridgeError("Blender took too long to answer %s." % cmd)
    finally:
        sock.close()
    if not line:
        raise BridgeError("Blender closed the connection.")
    response = json.loads(line)
    if not response.get("ok"):
        message = response.get("error", "Unknown error")
        if response.get("traceback"):
            message += "\n" + response["traceback"]
        raise BridgeError(message)
    return response.get("result")


def _text(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def call_tool(name, args):
    if name not in {t["name"] for t in TOOLS}:
        return _text("Unknown tool: %s" % name, True)
    try:
        result = call_blender(name, args or {})
    except BridgeError as ex:
        return _text(str(ex), True)

    if name == "get_viewport_screenshot":
        return {"content": [
            {"type": "image", "data": result["image_base64"], "mimeType": result["mime_type"]},
            {"type": "text", "text": "Viewport %dx%d (view: %s)"
                                     % (result["width"], result["height"], result["view"])},
        ]}
    if name == "execute_python":
        parts = []
        if result.get("output"):
            parts.append(result["output"].rstrip())
        if "result" in result:
            parts.append("result = " + json.dumps(result["result"], indent=1))
        if not parts:
            parts.append("Done (no output).")
        return _text("\n".join(parts), not result.get("ok", True))
    return _text(json.dumps(result, indent=1))


def handle(msg):
    method = msg.get("method")
    if "id" not in msg:
        return None  # notification
    mid = msg["id"]
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments"))
    elif method in ("resources/list", "prompts/list"):
        result = {method.split("/")[0]: []}
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "Method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                response = handle(msg) if isinstance(msg, dict) else None
            except Exception as ex:  # keep the server alive whatever happens
                response = {"jsonrpc": "2.0", "id": msg.get("id"),
                            "error": {"code": -32603, "message": str(ex)}}
        if response is not None:
            out.write((json.dumps(response) + "\n").encode("utf-8"))
            out.flush()


if __name__ == "__main__":
    main()
