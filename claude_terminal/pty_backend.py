# SPDX-License-Identifier: GPL-3.0-or-later
"""Pseudo-terminal process backends: ConPTY on Windows, openpty elsewhere.

All reads are non-blocking so they can run from a Blender timer on the main
thread without freezing the UI.
"""

import os
import shutil
import signal
import subprocess
import sys


class PtyError(RuntimeError):
    pass


def _resolve(exe, env):
    found = shutil.which(exe, path=env.get("PATH"))
    if not found:
        raise PtyError("Command not found: %s" % exe)
    return found


if sys.platform == "win32":

    class Pty:
        def __init__(self, argv, cwd, env, cols, rows):
            try:
                from winpty import PTY
            except ImportError as ex:
                raise PtyError("pywinpty is missing (%s). Reinstall the add-on." % ex)
            app = _resolve(argv[0], env)
            env_block = "\0".join("%s=%s" % kv for kv in env.items()) + "\0"
            self._pty = PTY(cols, rows)
            if len(argv) > 1:
                cmdline = " " + subprocess.list2cmdline(argv[1:])
                ok = self._pty.spawn(app, cmdline=cmdline, cwd=cwd, env=env_block)
            else:
                ok = self._pty.spawn(app, cwd=cwd, env=env_block)
            if not ok:
                raise PtyError("Could not start: %s" % app)
            self.pid = self._pty.pid

        def read(self):
            if self._pty is None:
                return ""
            chunks = []
            try:
                for _ in range(64):
                    data = self._pty.read(blocking=False)
                    if not data:
                        break
                    chunks.append(data)
            except Exception:
                pass  # EOF after the process exits
            return "".join(chunks)

        def write(self, text):
            if self._pty is not None and text:
                try:
                    self._pty.write(text)
                except Exception:
                    pass

        def resize(self, cols, rows):
            if self._pty is not None:
                try:
                    self._pty.set_size(cols, rows)
                except Exception:
                    pass

        def isalive(self):
            return self._pty is not None and self._pty.isalive()

        def exit_status(self):
            return None if self._pty is None else self._pty.get_exitstatus()

        def close(self):
            if self._pty is None:
                return
            if self.pid and self._pty.isalive():
                # Kill the whole tree: shell -> node (claude) -> tools.
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self._pty = None  # dropping it closes the pseudo console

else:
    import codecs
    import fcntl
    import struct
    import termios

    class Pty:
        def __init__(self, argv, cwd, env, cols, rows):
            app = _resolve(argv[0], env)
            master, slave = os.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

            def _child_setup():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            try:
                self._proc = subprocess.Popen(
                    [app] + list(argv[1:]), cwd=cwd, env=env,
                    stdin=slave, stdout=slave, stderr=slave,
                    preexec_fn=_child_setup, close_fds=True,
                )
            finally:
                os.close(slave)
            os.set_blocking(master, False)
            self._fd = master
            self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self.pid = self._proc.pid

        def read(self):
            if self._fd is None:
                return ""
            chunks = []
            try:
                for _ in range(64):
                    data = os.read(self._fd, 65536)
                    if not data:
                        break
                    chunks.append(data)
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                pass  # EIO once the child is gone
            return self._decoder.decode(b"".join(chunks)) if chunks else ""

        def write(self, text):
            if self._fd is None or not text:
                return
            data = text.encode("utf-8")
            while data:
                try:
                    n = os.write(self._fd, data)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                data = data[n:]

        def resize(self, cols, rows):
            if self._fd is not None:
                fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        def isalive(self):
            return self._proc.poll() is None

        def exit_status(self):
            return self._proc.poll()

        def close(self):
            if self._proc.poll() is None:
                try:
                    os.killpg(self._proc.pid, signal.SIGHUP)
                except OSError:
                    pass
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
