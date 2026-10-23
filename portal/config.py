"""systems.yaml -> plain objects. Read once at start; a broken file stops the portal with a clear message."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "systems.yaml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Server:
    id: str
    name: str
    folder: str
    python: str
    args: tuple[str, ...]
    port: int

    def python_path(self) -> Path:
        p = Path(self.python)
        return p if p.is_absolute() else Path(self.folder) / p


@dataclass(frozen=True)
class View:
    id: str
    label: str
    label_en: str
    icon: str
    server: Optional[str] = None
    path: str = "/"
    url: str = ""

    @property
    def external(self) -> bool:
        return self.server is None

    def text(self, lang: str) -> str:
        return self.label_en if lang == "en" else self.label


@dataclass(frozen=True)
class Link:
    label: str
    label_en: str
    view: str
    path: Optional[str]
    tag: str
    icon: str

    def text(self, lang: str) -> str:
        return self.label_en if lang == "en" else self.label


@dataclass
class Config:
    port: int
    owner: str
    servers: dict[str, Server]
    views: dict[str, View]
    quick: list[Link] = field(default_factory=list)
    flow: list[Link] = field(default_factory=list)


def safe_path(path: Optional[str]) -> str:
    """A page inside a system: must start with one '/', never '//' (that would be another host)."""
    p = (path or "/").strip()
    if not p.startswith("/") or p.startswith("//") or "\\" in p or any(ord(c) < 32 for c in p):
        return "/"
    return p


def _req(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"systems.yaml: '{key}' is missing in {where}")
    return d[key]


def load(path: Optional[Path] = None) -> Config:
    path = Path(path or os.getenv("KUPFER_PORTAL_CONFIG") or DEFAULT_FILE)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"systems.yaml not found at {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"systems.yaml is not valid YAML: {e}") from e

    portal = raw.get("portal") or {}
    servers: dict[str, Server] = {}
    for i, s in enumerate(raw.get("servers") or []):
        where = f"servers[{i}]"
        sid = str(_req(s, "id", where))
        if sid in servers:
            raise ConfigError(f"systems.yaml: server id '{sid}' appears twice")
        servers[sid] = Server(
            id=sid,
            name=str(s.get("name") or sid),
            folder=str(_req(s, "folder", where)),
            python=str(_req(s, "python", where)),
            args=tuple(str(a) for a in (s.get("args") or [])),
            port=int(_req(s, "port", where)),
        )

    views: dict[str, View] = {}
    for i, v in enumerate(raw.get("views") or []):
        where = f"views[{i}]"
        vid = str(_req(v, "id", where))
        server = v.get("server")
        if server is not None and server not in servers:
            raise ConfigError(f"systems.yaml: view '{vid}' points at unknown server '{server}'")
        label = str(_req(v, "label", where))
        views[vid] = View(
            id=vid, label=label, label_en=str(v.get("label_en") or label), icon=str(v.get("icon") or vid),
            server=server, path=safe_path(v.get("path")), url=str(v.get("url") or ""),
        )

    def links(key: str) -> list[Link]:
        out = []
        for i, q in enumerate(raw.get(key) or []):
            where = f"{key}[{i}]"
            view = str(_req(q, "view", where))
            if view not in views:
                raise ConfigError(f"systems.yaml: {where} points at unknown view '{view}'")
            label = str(_req(q, "label", where))
            out.append(Link(label=label, label_en=str(q.get("label_en") or label), view=view,
                            path=safe_path(q["path"]) if q.get("path") else None,
                            tag=str(q.get("tag") or ""), icon=str(q.get("icon") or views[view].icon)))
        return out

    return Config(port=int(portal.get("port") or 8080), owner=str(portal.get("owner") or ""),
                  servers=servers, views=views, quick=links("quick"), flow=links("flow"))
