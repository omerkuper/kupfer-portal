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


def write_config(tmp: Path, portal_port: int, dummy_port: int, extra: str = "") -> Path:
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
quick:
  - label: פתיחה
    view: dummy
    path: /index
    tag: DUMMY
flow:
  - label: שלב
    view: dummy
    tag: DUMMY
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
