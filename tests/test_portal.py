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
from portal.supervisor import CRASHED, EXTERNAL, RUNNING, STOPPED, Supervisor, port_open  # noqa: E402


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
    assert cfg.servers["rfq"].port != cfg.servers["poirot"].port      # both default to 8000 on their own
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
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs")
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
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: port_open(port))
        sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs")
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
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs")
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
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs")
    st = sup.start("gone")
    assert st.state == STOPPED and "folder not found" in st.error


# ---------------------------------------------------------------------- the web page
@pytest.fixture()
def portal(tmp_path):
    pport, dport = free_port(), free_port()
    cfg = config_mod.load(write_config(tmp_path, pport, dport))
    sup = Supervisor(cfg.servers, log_dir=tmp_path / "logs")
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
