"""Start the Kupfer portal (PyCharm: right-click -> Run 'main'):

    python main.py                 opens http://localhost:8080 in the browser
    python main.py --no-browser

Close it with Ctrl+C: it also stops the systems it started.
"""
import argparse
import sys
import threading
import time
import webbrowser

import uvicorn
from dotenv import load_dotenv

from portal.config import ConfigError, load

load_dotenv()   # the portal's own .env (KUPFER_PORTAL_KEY, KUPFER_SESSION_SECRET, KUPFER_SESSION_HOURS)
from portal.supervisor import port_open


def announce(port: int, open_browser: bool) -> None:
    """Wait until the portal answers, then say where it is (and open it)."""
    url = f"http://localhost:{port}"
    for _ in range(100):
        if port_open(port):
            print(f"\n  Kupfer portal is running:  {url}\n  Ctrl+C to stop (the systems it started stop with it)\n",
                  flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        time.sleep(0.1)


def run() -> None:
    parser = argparse.ArgumentParser(description="Kupfer portal")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()
    try:
        cfg = load()
    except ConfigError as e:
        sys.exit(f"Kupfer portal: {e}")
    if port_open(cfg.port):
        sys.exit(f"Kupfer portal: port {cfg.port} is already in use - is the portal open in another window?")
    threading.Thread(target=announce, args=(cfg.port, not args.no_browser), daemon=True).start()
    # 127.0.0.1 only: the portal can start programs on this computer, so nothing outside it may reach it.
    uvicorn.run("portal.asgi:app", host="127.0.0.1", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    run()
