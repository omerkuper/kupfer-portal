"""What you opened from the portal lately - kept in data/recent.json on this computer only."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "recent.json"
KEEP = 8


class Recent:
    def __init__(self, path: Path = DATA_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            self._items = [i for i in items if isinstance(i, dict) and "view" in i][:KEEP]
        except (OSError, ValueError):
            self._items = []

    def add(self, view: str, path: str) -> None:
        with self._lock:
            self._items = [i for i in self._items if (i["view"], i.get("path")) != (view, path)]
            self._items.insert(0, {"view": view, "path": path, "at": time.time()})
            del self._items[KEEP:]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass   # a list of shortcuts - never worth failing a page for

    def items(self) -> list[dict]:
        with self._lock:
            return list(self._items)
