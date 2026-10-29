"""One sign-in for every system (stage 2 of the unification).

The portal asks for one key (KUPFER_PORTAL_KEY) and puts a signed cookie, `kupfer_session`, on the browser. A cookie
belongs to a host name, not to a port, so the cookie set on localhost:8080 reaches the RFQ desk on localhost:8000 as
well - the desk accepts it beside its own DESK_KEY sign-in (app/security.py there, the same algorithm, the same
secret). No database, no JWT: the cookie is "<expiry>.<signature>", the signature an HMAC-SHA256 of the expiry with
KUPFER_SESSION_SECRET. Changing the secret signs everyone out everywhere.

Off by default: with no KUPFER_PORTAL_KEY the portal has no sign-in page and behaves as before. Nothing between
the systems (bookings, milestones, tracking) uses this cookie - those run on their own server-to-server tokens and
never stop when the session ends.

    KUPFER_PORTAL_KEY=...        the key typed on the sign-in page; empty = no sign-in
    KUPFER_SESSION_SECRET=...    a long random string; the SAME value in the RFQ desk's .env
    KUPFER_SESSION_HOURS=12      how long a sign-in lasts (12: one working day)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Mapping, Optional

COOKIE = "kupfer_session"
PREFIX = b"kupfer-session:"
DEFAULT_HOURS = 12.0
MAX_HOURS = 24.0 * 30


def sign(secret: str, expires: int) -> str:
    return hmac.new(secret.encode("utf-8"), PREFIX + str(int(expires)).encode("ascii"), hashlib.sha256).hexdigest()[:40]


def make(secret: str, hours: float, now: Optional[float] = None) -> str:
    """The cookie value: "<expiry>.<signature>" - the expiry a unix time, in seconds."""
    expires = int((time.time() if now is None else now) + float(hours) * 3600)
    return f"{expires}.{sign(secret, expires)}"


def valid(secret: str, cookie: Optional[str], now: Optional[float] = None) -> bool:
    """True when the cookie was signed with this secret and has not expired. An empty secret trusts nothing."""
    if not secret or not cookie:
        return False
    expires_text, _, signature = str(cookie).strip().partition(".")
    if not expires_text.isdigit() or not signature:
        return False
    expires = int(expires_text)
    if expires <= (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sign(secret, expires), signature)


@dataclass(frozen=True)
class Session:
    key: str = ""          # KUPFER_PORTAL_KEY - what is typed on the sign-in page
    secret: str = ""       # KUPFER_SESSION_SECRET - signs the cookie
    hours: float = DEFAULT_HOURS

    @property
    def on(self) -> bool:
        return bool(self.key)

    def key_matches(self, given: Optional[str]) -> bool:
        return self.on and hmac.compare_digest(str(given or "").strip().encode("utf-8"), self.key.encode("utf-8"))

    def cookie_value(self, now: Optional[float] = None) -> str:
        return make(self.secret, self.hours, now)

    def signed_in(self, cookie: Optional[str], now: Optional[float] = None) -> bool:
        return valid(self.secret, cookie, now)

    @property
    def max_age(self) -> int:
        return int(self.hours * 3600)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Session":
        env = os.environ if env is None else env
        key = str(env.get("KUPFER_PORTAL_KEY") or "").strip()
        secret = str(env.get("KUPFER_SESSION_SECRET") or "").strip()
        if key and not secret:
            # A key without a secret would sign cookies with nothing: derive one from the key so the portal still
            # works, and say so - but the desk cannot share it, so set KUPFER_SESSION_SECRET for the one sign-in.
            secret = hashlib.sha256(b"kupfer-portal:" + key.encode("utf-8")).hexdigest()
        return cls(key=key, secret=secret, hours=_hours(env.get("KUPFER_SESSION_HOURS")))


def _hours(value: Optional[str]) -> float:
    try:
        hours = float(str(value or "").strip() or DEFAULT_HOURS)
    except ValueError:
        return DEFAULT_HOURS
    return hours if 0 < hours <= MAX_HOURS else DEFAULT_HOURS
