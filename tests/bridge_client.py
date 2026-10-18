"""Send one command to the Claude Terminal bridge (for testing).

    python bridge_client.py <port> <token> <cmd> [json params | @file.py] [out.png]

For execute_python, "@file.py" sends the file's contents as the code.
For get_viewport_screenshot, the image is written to out.png.
"""
import base64
import json
import socket
import sys

port, token, cmd = int(sys.argv[1]), sys.argv[2], sys.argv[3]
arg = sys.argv[4] if len(sys.argv) > 4 else "{}"
if arg.startswith("@"):
    with open(arg[1:], encoding="utf-8-sig") as f:
        params = {"code": f.read()}
elif "=" in arg and not arg.startswith("{"):
    # key=value,key=value (PowerShell mangles JSON quotes on the command line)
    params = {}
    for pair in arg.split(","):
        key, value = pair.split("=", 1)
        params[key] = int(value) if value.lstrip("-").isdigit() else value
else:
    params = json.loads(arg)

sock = socket.create_connection(("127.0.0.1", port), timeout=120)
sock.sendall((json.dumps({"token": token, "cmd": cmd, "params": params}) + "\n").encode())
line = sock.makefile("rb").readline()
resp = json.loads(line)
result = resp.get("result") or {}
if isinstance(result, dict) and "image_base64" in result:
    out = sys.argv[5] if len(sys.argv) > 5 else "shot.png"
    with open(out, "wb") as f:
        f.write(base64.b64decode(result.pop("image_base64")))
    result["saved"] = out
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(resp, indent=1)[:20000])
