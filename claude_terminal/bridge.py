# SPDX-License-Identifier: GPL-3.0-or-later
"""Local TCP bridge between the MCP server process and Blender.

Protocol: one JSON object per line.
    request:  {"token": "...", "cmd": "execute_python", "params": {...}}
    response: {"ok": true, "result": {...}}  or  {"ok": false, "error": "..."}

Sockets are handled on background threads. Every command runs on Blender's
main thread from a timer, because bpy is not thread safe.
"""

import hmac
import json
import queue
import socket
import threading
import time
import traceback

import bpy

HOST = "127.0.0.1"
JOB_TIMEOUT = 600.0


class _Job:
    __slots__ = ("cmd", "params", "done", "response")

    def __init__(self, cmd, params):
        self.cmd = cmd
        self.params = params
        self.done = threading.Event()
        self.response = None


class Bridge:
    def __init__(self):
        self.sock = None
        self.port = None
        self.token = ""
        self.error = ""
        self.last_command = ""
        self._jobs = queue.Queue()
        self._stop = threading.Event()

    @property
    def running(self):
        return self.sock is not None

    def start(self, port, token):
        if self.running:
            return True
        self.error = ""
        sock = None
        for candidate in range(port, port + 10):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind((HOST, candidate))
                break
            except OSError:
                sock.close()
                sock = None
        if sock is None:
            self.error = "Ports %d-%d are busy" % (port, port + 9)
            return False
        sock.listen(8)
        sock.settimeout(0.5)
        self.sock, self.port, self.token = sock, candidate, token
        self._stop.clear()
        threading.Thread(target=self._accept_loop, args=(sock,), daemon=True,
                         name="claude-terminal-bridge").start()
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=0.05, persistent=True)
        return True

    def stop(self):
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            job.response = {"ok": False, "error": "The Blender bridge was stopped"}
            job.done.set()
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)

    # -- background threads ------------------------------------------------

    def _accept_loop(self, sock):
        while not self._stop.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(None)
        try:
            with conn, conn.makefile("rb") as rfile:
                for raw in rfile:
                    if self._stop.is_set():
                        break
                    response, keep_open = self._handle_line(raw)
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                    if not keep_open:
                        break
        except OSError:
            pass

    def _handle_line(self, raw):
        try:
            req = json.loads(raw)
        except ValueError:
            return {"ok": False, "error": "Invalid JSON"}, False
        if not isinstance(req, dict):
            return {"ok": False, "error": "Invalid request"}, False
        if not hmac.compare_digest(str(req.get("token", "")).encode(), self.token.encode()):
            return {"ok": False, "error": "Wrong bridge token. Run the setup command again "
                                          "from Blender's Claude panel."}, False
        job = _Job(str(req.get("cmd", "")), req.get("params") or {})
        self._jobs.put(job)
        if not job.done.wait(JOB_TIMEOUT):
            return {"ok": False, "error": "Blender did not answer in time (is it busy?)"}, True
        return job.response, True

    # -- main thread ---------------------------------------------------------------

    def process_jobs(self, budget=0.25):
        from . import tools

        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            self.last_command = job.cmd
            try:
                job.response = {"ok": True, "result": tools.run(job.cmd, job.params)}
            except Exception as ex:
                job.response = {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex),
                                "traceback": traceback.format_exc(limit=4)}
            job.done.set()


_bridge = Bridge()


def get():
    return _bridge


def _tick():
    if not _bridge.running:
        return None
    _bridge.process_jobs()
    return 0.05
