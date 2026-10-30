"""Offline tests: dummy "systems" are tiny Python web servers on free ports - nothing real is started.

    python -m pytest -q
"""
from __future__ import annotations

import socket
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portal import config as config_mod  # noqa: E402
from portal.app import create_app  # noqa: E402
from portal.recent import Recent  # noqa: E402
from portal import session as session_mod  # noqa: E402
from portal import summary as summary_mod  # noqa: E402
from portal.supervisor import (CONFLICT, CRASHED, EXTERNAL, RUNNING, STOPPED, Supervisor,  # noqa: E402
                               port_open)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def write_config(tmp: Path, portal_port: int, dummy_port: int, extra: str = "", views_extra: str = "",
                 flow_extra: str = "") -> Path:
    py = sys.executable.replace("\\", "/")
    (tmp / "whoami.txt").write_text("dummy-ok", encoding="utf-8")
    text = f"""
portal:
  port: {portal_port}
  owner: עומר
servers:
  - id: dummy
    name: Dummy
    folder: '{tmp.as_posix()}'
    python: '{py}'
    args: ["-m", "http.server", "{dummy_port}", "--bind", "127.0.0.1"]
    port: {dummy_port}
    probe: {{path: /whoami.txt, expect: dummy-ok}}
{extra}
views:
  - id: dummy
    label: דמה
    label_en: Dummy
    server: dummy
    path: /
  - id: calculator
    label: מחשבון
    label_en: Calculator
    url: ""
{views_extra}
quick:
  - label: פתיחה
    view: dummy
    path: /index
    tag: DUMMY
flow:
  - label: שלב
    view: dummy
    tag: DUMMY
{flow_extra}
"""
    path = tmp / "systems.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- config
def test_the_real_systems_yaml_loads():
    cfg = config_mod.load(ROOT / "systems.yaml")
    assert {"poirot", "rfq", "tracker", "tracker_api"} <= set(cfg.servers)
    # as the systems' .env files expect: the desk on 8000, Poirot on 8001
    assert cfg.servers["rfq"].port == 8000 and cfg.servers["poirot"].port == 8001
    assert all(s.probe_path and s.probe_expect for s in cfg.servers.values())
    assert cfg.views["sim"].server == "rfq"
    assert cfg.views["calculator"].external
    assert all(q.view in cfg.views for q in cfg.quick + cfg.flow)


@pytest.mark.parametrize("bad", ["//evil.com/x", "http://evil.com", "evil", "/a\\b", "/a\nb", ""])
def test_safe_path_keeps_pages_inside_the_system(bad):
    assert config_mod.safe_path(bad) == "/"


def test_safe_path_keeps_a_real_page():
    assert config_mod.safe_path("/desk/simulation?x=1") == "/desk/simulation?x=1"


def test_unknown_server_in_a_view_is_refused(tmp_path):
    p = tmp_path / "systems.yaml"
    p.write_text("servers: []\nviews:\n  - {id: a, label: A, server: nope}\n", encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(p)


# ---------------------------------------------------------------------- supervisor
def test_start_stop_and_logs(tmp_path):
    port = free_port()
    cfg = config_mod.load(write_config(tmp_path, free_port(), port))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    assert sup.status("dummy").state == STOPPED
    sup.start("dummy")
    assert wait_for(lambda: sup.status("dummy").state == RUNNING)
    sup.stop("dummy")
    assert sup.status("dummy").state == STOPPED
    assert not port_open(port)
    assert "started by Kupfer portal" in sup.tail("dummy")


def test_a_server_started_elsewhere_is_seen_but_never_stopped(tmp_path):
    import subprocess
    port = free_port()
    cfg = config_mod.load(write_config(tmp_path, free_port(), port))
    outside = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                               cwd=str(tmp_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: port_open(port))
        sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
        assert sup.status("dummy").state == EXTERNAL
        assert sup.start("dummy").state == EXTERNAL       # nothing new is started
        sup.stop("dummy")
        assert outside.poll() is None                      # still alive
    finally:
        outside.kill()
        outside.wait()


def test_a_crash_is_reported_with_its_code(tmp_path):
    port = free_port()
    extra = f"""  - id: broken
    name: Broken
    folder: '{tmp_path.as_posix()}'
    python: '{sys.executable.replace(chr(92), "/")}'
    args: ["-c", "import sys; sys.exit(3)"]
    port: {free_port()}
"""
    cfg = config_mod.load(write_config(tmp_path, free_port(), port, extra))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    sup.start("broken")
    assert wait_for(lambda: sup.status("broken").state == CRASHED)
    assert sup.status("broken").exit_code == 3


def test_a_missing_folder_is_a_clear_error(tmp_path):
    extra = f"""  - id: gone
    name: Gone
    folder: '{(tmp_path / "nope").as_posix()}'
    python: python
    args: []
    port: {free_port()}
"""
    cfg = config_mod.load(write_config(tmp_path, free_port(), free_port(), extra))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    st = sup.start("gone")
    assert st.state == STOPPED and "folder not found" in st.error


# ---------------------------------------------------------------------- the web page
@pytest.fixture()
def portal(tmp_path):
    pport, dport = free_port(), free_port()
    cfg = config_mod.load(write_config(tmp_path, pport, dport))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    app = create_app(cfg, sup, Recent(tmp_path / "recent.json"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=pport, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert wait_for(lambda: port_open(pport))
    yield f"http://localhost:{pport}", sup
    server.should_exit = True
    th.join(timeout=10)
    sup.stop_all()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8")


def post(url, origin):
    req = urllib.request.Request(url, data=b"", method="POST", headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_home_page_renders_in_hebrew(portal):
    base, _ = portal
    status, body = get(base + "/")
    assert status == 200
    assert 'dir="rtl"' in body and "מה פותחים עכשיו, עומר?" in body
    assert "מסלול העבודה" in body and "Dummy" in body


def test_english_switch(portal):
    base, _ = portal
    status, body = get(base + "/", headers={"Cookie": "kp_lang=en"})
    assert 'dir="ltr"' in body and "What are we opening" in body


def test_a_post_from_another_site_is_refused(portal):
    base, sup = portal
    assert post(base + "/servers/dummy/start", "https://evil.example")[0] == 403
    assert post(base + "/servers/dummy/start", None)[0] == 403
    assert sup.status("dummy").state == STOPPED


def test_another_host_name_is_refused(portal):
    base, _ = portal
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/", headers={"Host": "evil.example"})
    assert e.value.code == 403


def test_tab_offers_start_then_shows_the_system(portal):
    base, sup = portal
    _, body = get(base + "/partials/tab/dummy?path=/")
    assert "Dummy כבוי" in body and "hx-post=\"/servers/dummy/start" in body
    status, body = post(base + "/servers/dummy/start?view=dummy&path=/", base)
    assert status == 200
    assert wait_for(lambda: sup.status("dummy").state == RUNNING)
    _, body = get(base + "/partials/tab/dummy?path=/index")
    assert "<iframe" in body and "localhost:" in body and "/index" in body
    _, recent = get(base + "/partials/recent")
    assert "/index" in recent


def test_unsafe_path_in_a_tab_falls_back_to_the_home_page(portal):
    base, sup = portal
    sup.start("dummy")
    assert wait_for(lambda: sup.status("dummy").state == RUNNING)
    _, body = get(base + "/partials/tab/dummy?path=//evil.com/x")
    assert "evil.com" not in body


def test_calculator_without_an_address_explains(portal):
    base, _ = portal
    _, body = get(base + "/partials/tab/calculator")
    assert "systems.yaml" in body


def test_status_partial_and_log(portal):
    base, sup = portal
    post(base + "/servers/dummy/start", base)
    assert wait_for(lambda: sup.status("dummy").state == RUNNING)
    _, body = get(base + "/partials/status", headers={"HX-Request": "true"})
    assert 'data-state="running"' in body and 'id="dot-dummy"' in body and "1/1" in body
    _, log = get(base + "/servers/dummy/log")
    assert "started by Kupfer portal" in log
    post(base + "/servers/all/stop", base)
    assert sup.status("dummy").state == STOPPED


def test_another_program_on_the_port_is_a_conflict_not_the_system(tmp_path):
    import subprocess
    port = free_port()
    cfg = config_mod.load(write_config(tmp_path, free_port(), port))
    other = tmp_path / "other"
    other.mkdir()                     # serves a folder without whoami.txt: answers, but not as the system
    outside = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                               cwd=str(other), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: port_open(port))
        sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
        assert sup.status("dummy").state == CONFLICT
        st = sup.start("dummy")
        assert st.state == CONFLICT and "taken by another program" in st.error
        assert not sup.owned("dummy")
    finally:
        outside.kill()
        outside.wait()


def test_a_new_portal_takes_back_what_the_old_one_left_running(tmp_path):
    port = free_port()
    cfg = config_mod.load(write_config(tmp_path, free_port(), port))
    old = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    old.start("dummy")
    assert wait_for(lambda: old.status("dummy").state == RUNNING)
    popen = old._procs["dummy"].popen          # the old portal is "killed": nothing is stopped

    new = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    assert new.owned("dummy") and new.status("dummy").state == RUNNING
    reaper = threading.Thread(target=popen.wait, daemon=True)   # (in a test the child is ours to reap)
    reaper.start()
    new.stop("dummy", wait=5)
    assert wait_for(lambda: not port_open(port))
    assert new.status("dummy").state == STOPPED
    assert "dummy" not in (tmp_path / "pids.json").read_text(encoding="utf-8")


def test_a_stale_pid_file_is_ignored(tmp_path):
    cfg = config_mod.load(write_config(tmp_path, free_port(), free_port()))
    (tmp_path / "pids.json").write_text('{"dummy": {"pid": 999999, "started": 1}}', encoding="utf-8")
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    assert not sup.owned("dummy") and sup.status("dummy").state == STOPPED


def test_conflict_shows_in_the_tab(portal, tmp_path):
    import subprocess
    base, sup = portal
    port = sup.servers["dummy"].port
    other = tmp_path / "other2"
    other.mkdir()
    outside = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                               cwd=str(other), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: port_open(port))
        _, body = get(base + "/partials/tab/dummy")
        assert "<iframe" not in body and f"פורט {port} תפוס" in body
    finally:
        outside.kill()
        outside.wait()


# ---------------------------------------------------------------------- the one sign-in (portal/session.py)
SECRET = "a-long-random-secret-for-the-tests"


def test_a_cookie_is_signed_and_expires():
    now = 1_800_000_000
    cookie = session_mod.make(SECRET, 12, now=now)
    assert session_mod.valid(SECRET, cookie, now=now + 11 * 3600)
    assert not session_mod.valid(SECRET, cookie, now=now + 12 * 3600 + 1), "expired"
    assert not session_mod.valid("another-secret", cookie, now=now), "another secret"
    expires, _, sig = cookie.partition(".")
    assert not session_mod.valid(SECRET, f"{int(expires) + 3600}.{sig}", now=now), "a moved expiry breaks the signature"
    assert not session_mod.valid(SECRET, "garbage", now=now) and not session_mod.valid(SECRET, "", now=now)
    assert not session_mod.valid("", cookie, now=now), "an empty secret trusts nothing"


def test_session_settings_come_from_the_environment():
    off = session_mod.Session.from_env({})
    assert not off.on
    on = session_mod.Session.from_env({"KUPFER_PORTAL_KEY": "k", "KUPFER_SESSION_SECRET": "s", "KUPFER_SESSION_HOURS": "8"})
    assert on.on and on.hours == 8 and on.key_matches("k") and not on.key_matches("K")
    assert session_mod.Session.from_env({"KUPFER_PORTAL_KEY": "k", "KUPFER_SESSION_HOURS": "nope"}).hours == 12
    derived = session_mod.Session.from_env({"KUPFER_PORTAL_KEY": "k"})
    assert derived.secret and derived.signed_in(derived.cookie_value()), "a key alone still signs its own cookies"


@pytest.fixture()
def locked_portal(tmp_path):
    pport, dport = free_port(), free_port()
    cfg = config_mod.load(write_config(tmp_path, pport, dport))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    ses = session_mod.Session(key="open-sesame", secret=SECRET, hours=12)
    app = create_app(cfg, sup, Recent(tmp_path / "recent.json"), session=ses)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=pport, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert wait_for(lambda: port_open(pport))
    yield f"http://localhost:{pport}", ses
    server.should_exit = True
    th.join(timeout=10)
    sup.stop_all()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def raw(url, method="GET", data=None, headers=None, origin=None):
    """A request that does not follow redirects: (status, headers, body)."""
    h = dict(headers or {})
    if origin:
        h["Origin"] = origin
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8")


def test_without_a_session_every_page_goes_to_the_sign_in(locked_portal):
    base, _ = locked_portal
    status, headers, _ = raw(base + "/")
    assert status == 303 and headers["Location"] == "/login?next=/"
    status, headers, _ = raw(base + "/partials/status", headers={"HX-Request": "true"})
    assert status == 401 and headers["HX-Redirect"] == "/login"
    status, _, _ = raw(base + "/servers/dummy/start", method="POST", data=b"", origin=base)
    assert status == 401
    status, _, body = raw(base + "/login")
    assert status == 200 and 'name="key"' in body and "כניסה" in body
    assert raw(base + "/healthz")[0] == 200 and raw(base + "/static/portal.css")[0] == 200


def test_the_right_key_signs_in_and_the_cookie_opens_the_pages(locked_portal):
    base, ses = locked_portal
    status, headers, _ = raw(base + "/login", method="POST", data=b"key=wrong&next=/", origin=base,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 303 and "error=1" in headers["Location"] and "Set-Cookie" not in headers
    status, headers, _ = raw(base + "/login", method="POST", data=b"key=open-sesame&next=/partials/status", origin=base,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 303 and headers["Location"] == "/partials/status"
    cookie = headers["Set-Cookie"]
    assert cookie.startswith("kupfer_session=") and "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    assert "Domain=" not in cookie, "host-only: the systems on the other ports receive it as well"
    value = cookie.split(";")[0].split("=", 1)[1]
    assert ses.signed_in(value)
    status, _, body = raw(base + "/", headers={"Cookie": f"kupfer_session={value}"})
    assert status == 200 and "מה פותחים עכשיו" in body and 'action="/logout"' in body
    status, _, _ = raw(base + "/login", headers={"Cookie": f"kupfer_session={value}"})
    assert status == 303, "already in: the sign-in page sends you on"


def test_an_expired_or_forged_cookie_is_not_a_session(locked_portal):
    base, ses = locked_portal
    old = session_mod.make(SECRET, 12, now=time.time() - 13 * 3600)
    assert raw(base + "/", headers={"Cookie": f"kupfer_session={old}"})[0] == 303
    forged = session_mod.make("not-the-secret", 12)
    assert raw(base + "/", headers={"Cookie": f"kupfer_session={forged}"})[0] == 303


def test_signing_out_clears_the_portal_and_the_desk_cookies(locked_portal):
    base, ses = locked_portal
    value = ses.cookie_value()
    status, headers, _ = raw(base + "/logout", method="POST", data=b"", origin=base,
                             headers={"Cookie": f"kupfer_session={value}"})
    assert status == 303 and headers["Location"] == "/login"
    cleared = headers.get_all("Set-Cookie")
    assert any(c.startswith("kupfer_session=") for c in cleared) and any(c.startswith("roark_desk=") for c in cleared)
    assert all("Max-Age=0" in c or "expires=" in c.lower() for c in cleared)


def test_with_no_key_the_portal_has_no_sign_in(portal):
    base, _ = portal
    assert raw(base + "/")[0] == 200
    assert raw(base + "/login")[0] == 303, "nothing to sign in to: sent home"
    _, _, body = raw(base + "/")
    assert 'action="/logout"' not in body


# ---------------------------------------------------------------------- the numbers (portal/summary.py)
RFQ_ANSWER = {"contract": "summary.v1", "system": "rfq", "rfq_open": 5, "rfq_waiting": 2, "quotes_by_email": 1,
              "offers_awaiting_client": 3, "price_exposures": 0, "agents": 7}
POIROT_ANSWER = {"contract": "summary.v1", "system": "poirot", "needs_you": 4, "files_open": 9, "on_the_way": 6,
                 "arrived": 1, "bookings_pending": 1, "mail_to_file": 0}
TRACKER_ANSWER = {"contract": "summary.v1", "system": "tracker", "active": 6, "sea": 5, "air": 1, "stale": 0,
                  "changes_24h": 2, "last_check_at": "2026-10-29T08:00:00+00:00"}


def fake_http(answers):
    calls = []

    def http(url, token, timeout):
        calls.append((url, token))
        return answers.get(url)
    http.calls = calls
    return http


def three_sources():
    return {"rfq": summary_mod.Source("rfq", "http://rfq/api/summary", "t-rfq"),
            "poirot": summary_mod.Source("poirot", "http://poirot/api/summary", "t-poirot"),
            "tracker_api": summary_mod.Source("tracker_api", "http://tracker/summary", "")}


def test_summaries_ask_each_system_with_its_token_and_remember_for_a_while():
    http = fake_http({"http://rfq/api/summary": RFQ_ANSWER, "http://poirot/api/summary": POIROT_ANSWER})
    clock = [100.0]
    sums = summary_mod.Summaries(three_sources(), http=http, cache_seconds=10, clock=lambda: clock[0])
    answers = sums.all()
    assert answers["rfq"] == RFQ_ANSWER and answers["poirot"] == POIROT_ANSWER and answers["tracker_api"] is None
    assert sorted(http.calls) == [("http://poirot/api/summary", "t-poirot"), ("http://rfq/api/summary", "t-rfq"),
                                  ("http://tracker/summary", "")]
    sums.all()
    assert len(http.calls) == 3, "within the cache window nothing is asked again"
    clock[0] += 11
    sums.all()
    assert len(http.calls) == 6


def test_a_system_without_a_token_is_not_asked_except_the_tracker(tmp_path):
    cfg = config_mod.load(ROOT / "systems.yaml")
    sums = summary_mod.Summaries.from_config(cfg.servers, env={"KUPFER_POIROT_TOKEN": "p"})
    assert sums.sources["poirot"].asked and sums.sources["poirot"].url == "http://127.0.0.1:8001/api/summary"
    assert not sums.sources["rfq"].asked, "no KUPFER_RFQ_TOKEN: the desk would only answer 401"
    assert sums.sources["tracker_api"].asked and sums.sources["tracker_api"].url == "http://127.0.0.1:8770/summary"


def test_home_numbers_read_the_contracts_and_tolerate_a_missing_system():
    n = summary_mod.home_numbers({"rfq": RFQ_ANSWER, "poirot": POIROT_ANSWER, "tracker_api": TRACKER_ANSWER})
    assert n["flow"] == {"rfq": 2, "sim": 3, "poirot": 9, "tracking": 6}
    assert n["needs"] == {"poirot": 4, "rfq": 3, "bookings": 1, "tracker": 0} and n["needs_total"] == 8
    n = summary_mod.home_numbers({"rfq": None, "poirot": POIROT_ANSWER, "tracker_api": None})
    assert n["flow"] == {"rfq": None, "sim": None, "poirot": 9, "tracking": 6} and n["needs"]["rfq"] is None
    assert n["needs_total"] == 5 and n["up"] == {"rfq": False, "poirot": True, "tracker_api": False}
    assert summary_mod.home_numbers({})["needs_total"] is None
    assert summary_mod.count({"needs_you": "4"}, "needs_you") is None, "a number is a number, never a string"
    assert summary_mod.count({"needs_you": True}, "needs_you") is None


@pytest.fixture()
def counted_portal(tmp_path):
    pport, dport = free_port(), free_port()
    cfg = config_mod.load(write_config(tmp_path, pport, dport, extra="""  - id: poirot
    name: Poirot
    folder: '%s'
    python: python
    args: []
    port: %d
  - id: rfq
    name: RFQ
    folder: '%s'
    python: python
    args: []
    port: %d
""" % (tmp_path.as_posix(), free_port(), tmp_path.as_posix(), free_port()), views_extra="""  - id: poirot
    label: Poirot
    server: poirot
  - id: rfq
    label: RFQ
    server: rfq
  - id: sim
    label: סימולציה
    server: rfq
    path: /desk/simulation
  - id: tracking
    label: מעקב
    server: dummy
""", flow_extra="""  - label: בקשות מחיר
    view: rfq
    tag: RFQ
  - label: הצעות ללקוח
    view: sim
    tag: SIM
  - label: בתפעול
    view: poirot
    tag: POIROT
  - label: מטענים בדרך
    view: tracking
    tag: TRACKER
"""))
    answers = {"http://rfq/api/summary": RFQ_ANSWER, "http://poirot/api/summary": POIROT_ANSWER}
    sums = summary_mod.Summaries(three_sources(), http=fake_http(answers), cache_seconds=0)
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs", pid_file=tmp_path / "pids.json")
    app = create_app(cfg, sup, Recent(tmp_path / "recent.json"), summaries=sums)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=pport, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert wait_for(lambda: port_open(pport))
    yield f"http://localhost:{pport}", answers
    server.should_exit = True
    th.join(timeout=10)
    sup.stop_all()


def test_the_flow_strip_and_the_needs_card_show_the_systems_numbers(counted_portal):
    base, answers = counted_portal
    _, home = get(base + "/")
    assert 'hx-get="/partials/flow"' in home and 'hx-get="/partials/needs"' in home and "טוען…" in home
    _, flow = get(base + "/partials/flow")
    assert 'class="flow-num live"' in flow
    assert ">2<" in flow and "ממתינות לטיפול שלך" in flow, "RFQs waiting"
    assert ">3<" in flow and "אצל הלקוח" in flow, "offers with the client"
    assert ">9<" in flow and "תיקים פתוחים" in flow, "files in operation"
    assert ">6<" in flow and "הפליגו או המריאו" in flow, "cargo on the way"
    _, needs = get(base + "/partials/needs")
    assert 'class="needs-row"' in needs and needs.count('class="needs-row"') == 3, "Poirot, bookings, RFQ; the tracker has 0"
    assert "דורשים החלטה ב-Poirot" in needs and 'data-open="poirot"' in needs
    assert "הזמנות חדשות לפתיחה" in needs and "ממתינים בשולחן ה-RFQ" in needs


def test_a_system_that_is_down_shows_a_dash_not_an_error(counted_portal):
    base, answers = counted_portal
    del answers["http://poirot/api/summary"]
    status, flow = get(base + "/partials/flow")
    assert status == 200 and "המערכת כבויה" in flow and "—" in flow
    assert ">2<" in flow, "the desk still answers"
    del answers["http://rfq/api/summary"]
    status, needs = get(base + "/partials/needs")
    assert status == 200 and "המערכות כבויות" in needs
