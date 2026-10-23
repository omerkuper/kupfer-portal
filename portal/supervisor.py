"""Start, stop and watch the systems' servers.

Only the commands in systems.yaml are ever run - nothing from a request. A server the portal did not start
(you ran it from PyCharm) shows as "running outside the portal": the portal sees it but never stops it.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional

from portal.config import Server

WINDOWS = sys.platform == "win32"
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

RUNNING = "running"      # answering on its port, started here
EXTERNAL = "external"    # answering on its port, started elsewhere
STARTING = "starting"    # started here, alive, port not open yet
CRASHED = "crashed"      # started here and exited by itself
STOPPED = "stopped"


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Proc:
    popen: subprocess.Popen
    started: float
    log: IO[bytes]
    stopping: bool = False


@dataclass
class Status:
    state: str
    since: Optional[float] = None    # when it was started (only for processes started here)
    exit_code: Optional[int] = None
    error: str = ""                  # why it could not start

    @property
    def up(self) -> bool:
        return self.state in (RUNNING, EXTERNAL)


class Supervisor:
    def __init__(self, servers: dict[str, Server], log_dir: Path = LOG_DIR) -> None:
        self.servers = servers
        self.log_dir = log_dir
        self._procs: dict[str, Proc] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ status
    def status(self, sid: str) -> Status:
        server = self.servers[sid]
        with self._lock:
            proc = self._procs.get(sid)
            error = self._errors.get(sid, "")
        is_open = port_open(server.port)
        if proc is None:
            return Status(EXTERNAL if is_open else STOPPED, error=error)
        code = proc.popen.poll()
        if code is None:
            return Status(RUNNING if is_open else STARTING, since=proc.started)
        if proc.stopping:
            return Status(EXTERNAL if is_open else STOPPED)
        return Status(CRASHED, since=proc.started, exit_code=code)

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

    # ------------------------------------------------------------------ start / stop
    def start(self, sid: str) -> Status:
        server = self.servers[sid]
        with self._lock:
            self._errors.pop(sid, None)
            proc = self._procs.get(sid)
            alive = proc is not None and proc.popen.poll() is None
            if proc is not None and not alive:
                self._procs.pop(sid, None)    # a crashed run: forget it and start afresh
                proc.log.close()
        if alive:
            return self.status(sid)
        if port_open(server.port):
            return Status(EXTERNAL)          # already up - nothing to do

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
            self._procs[sid] = Proc(popen=popen, started=time.time(), log=log)
        return self.status(sid)

    def stop(self, sid: str, wait: float = 8.0) -> Status:
        with self._lock:
            proc = self._procs.get(sid)
            if proc is not None:
                proc.stopping = True
        if proc is None:
            return self.status(sid)       # not ours: never touched
        _kill_tree(proc.popen, wait)
        try:
            proc.log.close()
        except OSError:
            pass
        with self._lock:
            self._procs.pop(sid, None)
        return self.status(sid)

    def restart(self, sid: str) -> Status:
        self.stop(sid)
        deadline = time.time() + 5
        while port_open(self.servers[sid].port) and time.time() < deadline:
            time.sleep(0.2)
        return self.start(sid)

    def owned(self, sid: str) -> bool:
        with self._lock:
            return sid in self._procs

    def stop_all(self) -> None:
        for sid in list(self._procs):
            self.stop(sid)

    def _fail(self, sid: str, message: str) -> Status:
        with self._lock:
            self._errors[sid] = message
        return Status(STOPPED, error=message)


def _kill_tree(popen: subprocess.Popen, wait: float) -> None:
    if popen.poll() is not None:
        return
    if WINDOWS:
        # /T takes the children too (uvicorn workers, a Playwright browser)
        subprocess.run(["taskkill", "/PID", str(popen.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(popen.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        popen.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        if not WINDOWS:
            try:
                os.killpg(popen.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        popen.wait(timeout=3)
