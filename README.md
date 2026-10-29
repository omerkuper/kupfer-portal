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

The portal prints its address and opens **http://localhost:8080** in the browser (`--no-browser` to skip). Always use `localhost`, not `127.0.0.1`: the systems inside the
tabs keep their sign-in cookies only when the host name matches.

Closing the portal (Ctrl+C) also stops whatever it started. If it is killed without that chance
(PyCharm's red Stop button), the next portal finds what it left running (`data/pids.json`) and can stop it. A system you started yourself (from
PyCharm, say) shows as "running outside the portal". The portal opens it but never stops it.

## Sign-in: one for the portal and the systems

Off until you set it. Put in `D:\kupfer-portal\.env` (never in git):

```
KUPFER_PORTAL_KEY=<the key you type on the sign-in page>
KUPFER_SESSION_SECRET=<a long random string>
KUPFER_SESSION_HOURS=12
```

and the same `KUPFER_SESSION_SECRET=...` line in the RFQ desk's `.env`. The portal then asks for the key once and
puts a signed cookie on `localhost`; the RFQ desk accepts that cookie beside its own `DESK_KEY` sign-in, so its tab
opens without asking again. Sign out (top right) clears both. A cookie belongs to a host name, not a port - which
is why `localhost`, never `127.0.0.1`. The session is only your browser's: the systems keep running, talking to
each other and to the mail when it ends. Locked out? Remove `KUPFER_PORTAL_KEY` from `.env` and restart the portal.
`portal/session.py` has the details.

## Settings: `systems.yaml`

- `servers`: what the portal can start. Folder, the venv's python, the command, the port, and a
  `probe`: a page that says who answers on the port. The ports match the systems' own `.env` files:
  **the RFQ desk on 8000, Poirot on 8001**. If another program sits on a system's port, the portal shows
  "port taken by another program" and never opens it in that system's tab.
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
| `portal/session.py` | the one sign-in: the signed `kupfer_session` cookie the RFQ desk honours too |
| `portal/i18n.py` | Hebrew (default) and English |
| `portal/static/portal.js` | the only script: tabs and Ctrl K |
| `logs/` | each system's output when started from the portal (not in git) |
| `data/recent.json` | "recently opened" (not in git) |
| `data/pids.json` | what the portal started, so a new portal can take it back (not in git) |

## Tests

```powershell
python -m pytest -q
```

The tests start small stand-in web servers on free ports. Nothing real is started.

## Phases

1. **Shell (this):** rail, tabs, start/stop/log, live status.
2. **Data on the home screen:** a small read-only summary endpoint in each system feeds the counts in
   the flow strip, "Needs you" and more. Each one is a change inside that system, shown before it is made.
3. **Smart search:** a B/L or AWB goes to tracking, a file number to Poirot, an RFQ number to the desk.

## After updating a system's code

A running server keeps the old Python code but reads its templates fresh from disk, so a page can break
until it restarts. Press restart (↻) on that system's card after every update.
