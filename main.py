"""Start the Kupfer portal (PyCharm: right-click -> Run 'main'), then open http://localhost:8080

    python main.py
"""
import sys

import uvicorn

from portal.config import ConfigError, load


def run() -> None:
    try:
        cfg = load()
    except ConfigError as e:
        sys.exit(f"Kupfer portal: {e}")
    # 127.0.0.1 only: the portal can start programs on this computer, so nothing outside it may reach it.
    uvicorn.run("portal.asgi:app", host="127.0.0.1", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    run()
