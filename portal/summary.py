"""The numbers on the home page (stage 4 of the unification, 29 Oct): each system is asked for its summary.v1 -
`GET /api/summary` on the RFQ desk and on Poirot, `GET /summary` on the tracker API - over that system's own
read token. The portal keeps none of it: a short cache so the page's 30-second refresh and a few open tabs do not
turn into a stream of calls, and nothing else.

A system that is down, slow (2 seconds) or refuses the token shows "—", never an error: the home page is the
portal's, the numbers are the systems'. Tokens, in the portal's .env:

    KUPFER_RFQ_TOKEN=...         the desk's POIROT_EVENTS_TOKEN (or its POIROT_INTAKE_TOKEN)
    KUPFER_POIROT_TOKEN=...      Poirot's POIROT_INTAKE_TOKEN (or POIROT_API_TOKEN)
    KUPFER_TRACKER_TOKEN=...     the tracker's TRACKER_API_TOKEN - leave empty when it has none

Without a token the system is not asked. The contracts' keys are listed in CONTRACTS.md; a key missing from an
answer reads as "—" here, so an older system never breaks the page.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

TIMEOUT = 2.0
CACHE_SECONDS = 10.0

# server id in systems.yaml -> (the summary's path, the .env variable with its token)
SOURCES = {"rfq": ("/api/summary", "KUPFER_RFQ_TOKEN"), "poirot": ("/api/summary", "KUPFER_POIROT_TOKEN"),
           "tracker_api": ("/summary", "KUPFER_TRACKER_TOKEN")}


@dataclass(frozen=True)
class Source:
    id: str
    url: str          # the summary's full address
    token: str        # "" = ask without one (the tracker without TRACKER_API_TOKEN)
    asked: bool = True   # False = no token configured for a system that needs one: not asked at all


Http = Callable[[str, str, float], Optional[dict]]


def _urllib_http(url: str, token: str, timeout: float) -> Optional[dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - his own systems on this computer
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


class Summaries:
    """Asks every source, remembers each answer for CACHE_SECONDS, and never raises."""

    def __init__(self, sources: Mapping[str, Source], *, http: Http = _urllib_http, timeout: float = TIMEOUT,
                 cache_seconds: float = CACHE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.sources = dict(sources)
        self.http, self.timeout, self.cache_seconds, self.clock = http, timeout, cache_seconds, clock
        self._cache: dict[str, tuple[float, Optional[dict]]] = {}
        self._lock = threading.Lock()

    def one(self, sid: str) -> Optional[dict]:
        src = self.sources.get(sid)
        if src is None or not src.asked:
            return None
        now = self.clock()
        with self._lock:
            hit = self._cache.get(sid)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        answer = self.http(src.url, src.token, self.timeout)
        with self._lock:
            self._cache[sid] = (now, answer)
        return answer

    def all(self) -> dict[str, Optional[dict]]:
        """Every source at once (threads), so three systems that are down cost one timeout, not three."""
        ids = list(self.sources)
        if len(ids) <= 1:
            return {sid: self.one(sid) for sid in ids}
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            answers = list(pool.map(self.one, ids))
        return dict(zip(ids, answers))

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()

    @classmethod
    def from_config(cls, servers: Mapping[str, Any], env: Optional[Mapping[str, str]] = None, **kw) -> "Summaries":
        """The sources the systems.yaml servers give: a server whose id SOURCES knows, at its port."""
        env = os.environ if env is None else env
        sources = {}
        for sid, (path, var) in SOURCES.items():
            server = servers.get(sid)
            if server is None:
                continue
            token = str(env.get(var) or "").strip()
            sources[sid] = Source(id=sid, url=f"http://127.0.0.1:{server.port}{path}", token=token,
                                  asked=bool(token) or sid == "tracker_api")
        return cls(sources, **kw)


def count(answer: Optional[dict], key: str) -> Optional[int]:
    """A number from an answer, or None - a missing key, a system that did not answer, a value that is not a number."""
    if not answer:
        return None
    value = answer.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def home_numbers(answers: Mapping[str, Optional[dict]]) -> dict[str, Any]:
    """What the home page shows, keyed as the templates read it. None = "—"."""
    rfq, poirot, tracker = answers.get("rfq"), answers.get("poirot"), answers.get("tracker_api")
    needs = {
        "poirot": count(poirot, "needs_you"),
        "rfq": _sum(count(rfq, "rfq_waiting"), count(rfq, "quotes_by_email"), count(rfq, "price_exposures")),
        "bookings": count(poirot, "bookings_pending"),
        "tracker": count(tracker, "stale"),
    }
    return {
        "flow": {"rfq": count(rfq, "rfq_waiting"), "sim": count(rfq, "offers_awaiting_client"),
                 "poirot": count(poirot, "files_open"), "tracking": count(poirot, "on_the_way")},
        "needs": needs,
        "needs_total": _sum(*needs.values()),
        "up": {"rfq": rfq is not None, "poirot": poirot is not None, "tracker_api": tracker is not None},
    }


def _sum(*values: Optional[int]) -> Optional[int]:
    known = [v for v in values if v is not None]
    return sum(known) if known else None
