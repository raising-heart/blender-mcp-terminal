"""Talk MCP (JSON-RPC over stdio) to mcp_server.py the way Claude Code does.

    python -I tests/test_mcp_server.py <port> <token>

Needs a running Blender with the Claude Terminal bridge.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "claude_terminal", "mcp_server.py")
port, token = sys.argv[1], sys.argv[2]
sys.stdout.reconfigure(encoding="utf-8")

env = dict(os.environ, BLENDER_BRIDGE_PORT=port, BLENDER_BRIDGE_TOKEN=token)
proc = subprocess.Popen([sys.executable, "-I", SERVER], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, env=env)
next_id = 0


def rpc(method, params=None, notify=False):
    global next_id
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        next_id += 1
        msg["id"] = next_id
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    if notify:
        return None
    return json.loads(proc.stdout.readline())


failed = 0


def check(name, cond):
    global failed
    print(("PASS " if cond else "FAIL ") + name)
    failed += 0 if cond else 1


init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}})
check("initialize", init["result"]["serverInfo"]["name"] == "blender"
      and "instructions" in init["result"])
rpc("notifications/initialized", notify=True)

tools = rpc("tools/list")["result"]["tools"]
check("tools/list", [t["name"] for t in tools] ==
      ["get_scene_info", "get_object_info", "execute_python", "get_viewport_screenshot"])

r = rpc("tools/call", {"name": "execute_python", "arguments": {
    "code": "import bpy\nbpy.ops.mesh.primitive_monkey_add(location=(0, 0, 3))\n"
            "bpy.context.active_object.name = 'McpMonkey'\nprint('made', bpy.context.active_object.name)\n"
            "result = len(bpy.data.objects)"}})["result"]
text = r["content"][0]["text"]
check("execute_python output", "made McpMonkey" in text and "result =" in text and not r.get("isError"))

r = rpc("tools/call", {"name": "execute_python", "arguments": {"code": "1/0"}})["result"]
check("execute_python error is reported", r.get("isError") and "ZeroDivisionError" in r["content"][0]["text"])

r = rpc("tools/call", {"name": "get_object_info", "arguments": {"name": "McpMonkey"}})["result"]
info = json.loads(r["content"][0]["text"])
check("get_object_info", info["mesh"]["faces"] == 500)

r = rpc("tools/call", {"name": "get_object_info", "arguments": {"name": "Nope"}})["result"]
check("missing object is an error", r.get("isError"))

r = rpc("tools/call", {"name": "get_scene_info", "arguments": {}})["result"]
scene = json.loads(r["content"][0]["text"])
check("get_scene_info", "McpMonkey" in [o["name"] for o in scene["objects"]])

r = rpc("tools/call", {"name": "get_viewport_screenshot",
                       "arguments": {"view": "FRONT", "frame": "ALL", "max_size": 640}})["result"]
img = r["content"][0]
check("screenshot is an image", img["type"] == "image" and img["mimeType"] == "image/png"
      and len(img["data"]) > 5000)
if img["type"] == "image":
    import base64
    with open(os.path.join(os.environ.get("TEMP", "."), "cct", "mcp_shot.png"), "wb") as f:
        f.write(base64.b64decode(img["data"]))

r = rpc("tools/call", {"name": "execute_python", "arguments": {
    "code": "bpy.data.objects.remove(bpy.data.objects['McpMonkey'])"}})["result"]
check("cleanup", not r.get("isError"))

check("unknown method", rpc("bogus/method")["error"]["code"] == -32601)
check("ping", rpc("ping")["result"] == {})

proc.stdin.close()
proc.wait(5)
print("failed:", failed)
sys.exit(1 if failed else 0)
