# Kupfer portal

One page to open, start and move between the systems: **Poirot**, the **RFQ desk** (with simulation,
tariffs and agents) and the **cargo tracker**. Each system opens as a tab inside the portal and keeps
its place when you switch tabs. The portal never changes the systems' code. It runs each one from its
own folder with its own `.venv`, the way you would from PyCharm.

Sketch C, "workspace + flow": a side rail, tabs, and a home screen with quick start, the four steps of
the work, and the systems' status.

## Run

```powershell
cd D:\kupfer-portal
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Then open **http://localhost:8080**. Always use `localhost`, not `127.0.0.1`: the systems inside the
tabs keep their sign-in cookies only when the host name matches.

Closing the portal (Ctrl+C) also stops whatever it started. A system you started yourself (from
PyCharm, say) shows as "running outside the portal". The portal opens it but never stops it.

## Settings: `systems.yaml`

- `servers`: what the portal can start. Folder, the venv's python, the command, and the port.
  Poirot and the RFQ desk both default to port 8000, so here **the RFQ desk runs on 8001**. If anything
  calls the desk at a fixed address (`POIROT_QUOTE_URL` in Poirot's `.env`, `PORTAL_BASE_URL` in the
  desk's `.env`), point it at 8001 too, or change the port here.
- `views`: the rail buttons. Each one is a system plus a page in it, or an outside address (`url`) that
  opens in a new window. **Fill in the calculator's Render address** under `calculator → url`.
- `quick`: the "quick start" list on the home screen.
- `flow`: the four steps of the work. In phase 1 they open the system. The counts arrive in phase 2.

## What is where

| | |
|---|---|
| `main.py` | starts the portal on 127.0.0.1 only |
| `portal/app.py` | the pages and the HTMX partials; a POST is accepted only from the portal's own page |
| `portal/supervisor.py` | start / stop / status / logs. Only commands from `systems.yaml` are ever run |
| `portal/config.py` | reads `systems.yaml` |
| `portal/i18n.py` | Hebrew (default) and English |
| `portal/static/portal.js` | the only script: tabs and Ctrl K |
| `logs/` | each system's output when started from the portal (not in git) |
| `data/recent.json` | "recently opened" (not in git) |

## Tests

```powershell
pip install pytest
python -m pytest -q
```

The tests start small stand-in web servers on free ports. Nothing real is started.

## Phases

1. **Shell (this):** rail, tabs, start/stop/log, live status.
2. **Data on the home screen:** a small read-only summary endpoint in each system feeds the counts in
   the flow strip, "Needs you" and more. Each one is a change inside that system, shown before it is made.
3. **Smart search:** a B/L or AWB goes to tracking, a file number to Poirot, an RFQ number to the desk.
