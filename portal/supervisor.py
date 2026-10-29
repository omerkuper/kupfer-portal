"""Start, stop and watch the systems' servers.

Only the commands in systems.yaml are ever run - nothing from a request.

Who is on the port? An open port is not enough: each server is asked who it is (its "probe" in systems.yaml).
If another program sits on the port, the portal says so and never opens it in the system's tab.

What did the portal start? It writes the process ids to data/pids.json. If the portal is killed without a chance
to stop them (PyCharm's red Stop button), the next portal finds them again and can stop them.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional

from portal.config import Server

WINDOWS = sys.platform == "win32"
BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / "logs"
PID_FILE = BASE / "data" / "pids.json"

RUNNING = "running"      # it is the right system, started here
EXTERNAL = "external"    # it is the right system, started elsewhere
STARTING = "starting"    # started here, alive, not answering yet
CONFLICT = "conflict"    # another program answers on the system's port
CRASHED = "crashed"      # started here and exited by itself
STOPPED = "stopped"

GRACE = 60.0             # seconds a freshly started server may answer the probe wrongly before it counts
PROBE_TTL = 60.0         # a good probe answer holds this long while the port stays open


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(server: Server, timeout: float = 2.0) -> bool:
    """Is it really this system on the port? No probe in systems.yaml: an open port is taken as yes."""
    if not server.probe_path:
        return True
    url = f"http://127.0.0.1:{server.port}{server.probe_path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:          # a 401 can still say who it is
        body = e.read(200_000).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return any(s in body for s in server.probe_expect)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259           # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Proc:
    """A process the portal started - now, or in an earlier run (adopted: no Popen, only its pid)."""

    def __init__(self, pid: int, started: float, popen: Optional[subprocess.Popen] = None,
                 log: Optional[IO[bytes]] = None) -> None:
        self.pid, self.started, self.popen, self.log = pid, started, popen, log
        self.stopping = False

    def poll(self) -> Optional[int]:
        """None while alive; the exit code once it ended (-1 when the code is unknown - an adopted process)."""
        if self.popen is not None:
            return self.popen.poll()
        return None if pid_alive(self.pid) else -1

    def close_log(self) -> None:
        if self.log is not None:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None


@dataclass
class Status:
    state: str
    since: Optional[float] = None    # when it was started (only for processes started by the portal)
    exit_code: Optional[int] = None
    error: str = ""                  # why it could not start

    @property
    def up(self) -> bool:
        return self.state in (RUNNING, EXTERNAL)


class Supervisor:
    def __init__(self, servers: dict[str, Server], log_dir: Path = LOG_DIR, pid_file: Path = PID_FILE) -> None:
        self.servers = servers
        self.log_dir = log_dir
        self.pid_file = pid_file
        self._procs: dict[str, Proc] = {}
        self._errors: dict[str, str] = {}
        self._ids: dict[str, tuple[bool, float]] = {}
        self._lock = threading.Lock()
        self._adopt()

    # ------------------------------------------------------------------ status
    def status(self, sid: str) -> Status:
        server = self.servers[sid]
        with self._lock:
            proc = self._procs.get(sid)
            error = self._errors.get(sid, "")
        is_open = port_open(server.port)
        if not is_open:
            self.forget_identity(sid)
        ours = is_open and self._identity(sid)
        if proc is None:
            if not is_open:
                return Status(STOPPED, error=error)
            return Status(EXTERNAL if ours else CONFLICT, error=error)
        code = proc.poll()
        if code is None:
            if not is_open:
                return Status(STARTING, since=proc.started)
            if not ours and time.time() - proc.started < GRACE:
                return Status(STARTING, since=proc.started)    # the port is up, the app not quite yet
            return Status(RUNNING if ours else CONFLICT, since=proc.started)
        if proc.stopping:
            return Status(STOPPED if not is_open else (EXTERNAL if ours else CONFLICT))
        return Status(CRASHED, since=proc.started, exit_code=code)

    def _identity(self, sid: str) -> bool:
        """The probe's answer, asked again only once a minute (or after the port was closed) - so the
        systems' own logs don't fill with a status check every 5 seconds."""
        now = time.time()
        with self._lock:
            hit = self._ids.get(sid)
        if hit and hit[0] and now - hit[1] < PROBE_TTL:
            return True
        ok = probe(self.servers[sid])
        with self._lock:
            self._ids[sid] = (ok, now)
        return ok

    def forget_identity(self, sid: str) -> None:
        with self._lock:
            self._ids.pop(sid, None)

    def all(self) -> dict[str, Status]:
        return {sid: self.status(sid) for sid in self.servers}

    def log_path(self, sid: str) -> Path:
        return self.log_dir / f"{sid}.log"

    def tail(self, sid: str, lines: int = 300) -> str:
        path = self.log_path(sid)
        if not path.exists():
            return ""
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read()
        return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-lines:])

    def owned(self, sid: str) -> bool:
        with self._lock:
            return sid in self._procs

    # ------------------------------------------------------------------ start / stop
    def start(self, sid: str) -> Status:
        server = self.servers[sid]
        with self._lock:
            self._errors.pop(sid, None)
            proc = self._procs.get(sid)
            alive = proc is not None and proc.poll() is None
            if proc is not None and not alive:
                self._procs.pop(sid, None)    # a crashed run: forget it and start afresh
                proc.close_log()
        if alive:
            return self.status(sid)
        if port_open(server.port):
            if probe(server):
                return Status(EXTERNAL)      # already up - nothing to do
            return self._fail(sid, f"port {server.port} is taken by another program", CONFLICT)

        folder = Path(server.folder)
        python = server.python_path()
        if not folder.is_dir():
            return self._fail(sid, f"folder not found: {folder}")
        if not python.exists():
            return self._fail(sid, f"python not found: {python}")

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_path(sid).open("wb")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log.write(f"--- {stamp} started by Kupfer portal: {python} {' '.join(server.args)}\n".encode())
        log.flush()

        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        env.pop("VIRTUAL_ENV", None)          # the portal's own venv must not leak into the system's
        kwargs: dict = {"cwd": str(folder), "stdout": log, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL, "env": env}
        if WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            popen = subprocess.Popen([str(python), *server.args], **kwargs)
        except OSError as e:
            log.close()
            return self._fail(sid, f"could not start: {e}")

        with self._lock:
            self._procs[sid] = Proc(popen.pid, time.time(), popen=popen, log=log)
            self._save()
        return self.status(sid)

    def stop(self, sid: str, wait: float = 8.0) -> Status:
        with self._lock:
            proc = self._procs.get(sid)
            if proc is not None:
                proc.stopping = True
        if proc is None:
            return self.status(sid)       # not ours: never touched
        _kill_tree(proc, wait)
        proc.close_log()
        with self._lock:
            self._procs.pop(sid, None)
            self._save()
        return self.status(sid)

    def restart(self, sid: str) -> Status:
        if not self.owned(sid) and port_open(self.servers[sid].port):
            return self.status(sid)       # someone else's process: restart it where it was started
        self.stop(sid)
        deadline = time.time() + 5
        while port_open(self.servers[sid].port) and time.time() < deadline:
            time.sleep(0.2)
        return self.start(sid)

    def stop_all(self) -> None:
        for sid in list(self._procs):
            self.stop(sid)

    def _fail(self, sid: str, message: str, state: str = STOPPED) -> Status:
        with self._lock:
            self._errors[sid] = message
        return Status(state, error=message)

    # ------------------------------------------------------------------ the pid file
    def _save(self) -> None:
        """Called with the lock held."""
        data = {sid: {"pid": p.pid, "started": p.started} for sid, p in self._procs.items()}
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _adopt(self) -> None:
        """Take back what an earlier portal started and left running. Only when the process is alive AND the
        right system answers on its port - a reused pid with nothing behind it is dropped."""
        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for sid, info in (data or {}).items():
            server = self.servers.get(sid)
            try:
                pid, started = int(info["pid"]), float(info.get("started") or time.time())
            except (KeyError, TypeError, ValueError):
                continue
            if server and pid_alive(pid) and port_open(server.port) and probe(server):
                self._procs[sid] = Proc(pid, started)
        with self._lock:
            self._save()


def _kill_tree(proc: Proc, wait: float) -> None:
    if proc.poll() is not None:
        return
    if WINDOWS:
        # /T takes the children too (the venv launcher's python, uvicorn workers, a Playwright browser)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)      # started with its own session: pgid == pid
        except (ProcessLookupError, PermissionError):
            return
    end = time.time() + wait
    while proc.poll() is None and time.time() < end:
        time.sleep(0.1)
    if proc.poll() is None and not WINDOWS:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        time.sleep(0.3)
    if proc.popen is not None:
        try:
            proc.popen.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
