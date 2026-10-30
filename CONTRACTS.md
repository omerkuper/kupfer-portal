# Kupfer – the contracts between the systems

One page that names every connection between the four systems: who calls whom, what the call carries, which
side owns the data, how it is secured, and what is still missing. Written at the start of the unification work
(stage 0, 29 Oct 2026) from the code as it stands; the tag `pre-unify` in every repository marks that state.

Rules that hold for every connection, present and future:

1. **Every system runs alone.** A connection that is not configured (its URL empty in `.env`) switches the feature
   off; nothing else changes. A system that is down makes the other show a clear message, never a 500.
2. **Only over HTTP + JSON, only with a version.** A contract has a name and a version (`booking.v1`); a change
   that breaks a reader gets a new version, and both sides keep a contract test.
3. **One owner per fact.** The owner keeps the data; everyone else asks. A copy kept elsewhere is a record of
   what was true at that moment, never the source.
4. **No connection prices, decides or sends mail.** Prices are computed in the quote system's Python; decisions
   are the operator's; mail leaves only from the system that owns the conversation.

## The systems

| System | Repo / folder | Runs as | Port | Owns |
|---|---|---|---|---|
| **Kupfer portal** | `D:\kupfer-portal` | `python main.py` (Starlette) | 8080 | the session (stage 2), navigation, start/stop of the others |
| **RFQ desk / quote system** | `D:\freight-engine-api 2\freight-engine-api-clean` | `uvicorn app.main:app` (FastAPI, Supabase) | 8000 | agent book, tariffs, RFQs and quotes, client offers, saved simulations, ADHOC quotes, price exposure |
| **Poirot** | `D:\poirot-project` | `uvicorn simulation_ui.serve:app` (FastAPI, SQLite) | 8001 | customers, suppliers of a file, files, documents, milestones, correspondence, Needs You |
| **Cargo tracker** | `D:\cargo-tracker` | `python api.py` (stdlib HTTP server) + `python dashboard.py` | 8770 / 8765 | live tracking events, the carrier and airline connections |

The ports are the ones the systems' own `.env` files expect; the portal's `systems.yaml` repeats them.

## Connections that exist today

### 1. RFQ → Poirot: the booking (`booking.v1`)

| | |
|---|---|
| Direction | RFQ desk **POSTs** to Poirot when the client books an offer |
| Endpoint | `POST {POIROT_URL}/api/intake/booking` · `GET /api/intake/booking/{rfq_id}` |
| Token | `POIROT_INTAKE_TOKEN` (RFQ `.env`) = `POIROT_INTAKE_TOKEN` or `POIROT_API_TOKEN` (Poirot `.env`), `Authorization: Bearer` |
| Carries | both quotes (the agent's = cost, the accepted offer = the client's price), parties, shipment, short-lived links to the documents |
| Owner after the call | Poirot owns the file, the customer card and the supplier; the agent stays the RFQ's (the primary contact travels as a record) |
| Code | RFQ: `app/services/poirot_handoff.py`, `booking_package.py` · Poirot: `poirot/operator/intake.py`, `poirot/web/app.py` (`/api/intake/*`) |
| Contract test | RFQ: `tests/test_offers_booking_flow.py` runs against Poirot's own reader when `POIROT_PROJECT` is set |
| Resilience today | RFQ keeps `status` (NOT_SENT / SENT / FAILED / REJECTED), `attempts`, `last_error`; *Send again* on the desk. Poirot answers a replay of the same package with `200 replay` (payload hash) and `409` once the booking was opened – **no duplicate file is possible**. |
| Retry | automatic since stage 5 (`app/services/handoff_retry.py`): 1, 5, 15, 60 min, then hourly, 24 attempts; then the desk |

### 2. Poirot → RFQ: milestones and the price basis (`docs/POIROT_API.md` in the RFQ repo)

| | |
|---|---|
| Direction | Poirot **POSTs** milestones and planned dates; **GETs** the exposure list every minute |
| Endpoints | `POST /api/poirot/milestones` · `GET /api/poirot/milestones/{rfq_id}` · `GET /api/poirot/price-basis/{rfq_id}` · `GET /api/poirot/exposure` |
| Token | `POIROT_QUOTE_URL` + `POIROT_QUOTE_TOKEN` (Poirot `.env`) = `POIROT_EVENTS_TOKEN` (falls back to `POIROT_INTAKE_TOKEN`) in RFQ `.env` |
| Carries | PICKED_UP / GATED_IN / SAILED / ARRIVED / DELIVERED, the planned ETD and cut-off; back: the stage, whether the carrier fixed the price, the exposure (rule ABSORB / APPROVE / CHECK, delta, lines) and `hold_invoice` |
| Owner | the exposure and its rule (flat 50 USD tolerance) are the RFQ's; the operator's decision is recorded on the RFQ desk; Poirot shows and never decides |
| Code | Poirot: `poirot/operator/price_changes.py` (`report`, `poll`, `run_once`) · RFQ: `app/routers/poirot_api.py`, `app/services/exposure.py` |
| Resilience today | each value is sent once and again only when it changed or was not acknowledged; a refusal that will not change (404/409/422) is remembered. A Poirot that was off asks when it comes back. **Already resilient; nothing to add in stage 5.** |

### 3. Poirot → Tracker: live status (`docs/tracker_api.md` in the Poirot repo)

| | |
|---|---|
| Direction | Poirot asks; the tracker never calls Poirot |
| Endpoints | `POST /track` · `GET /shipment/{ref}?carrier=&max_age=` · `GET /changes?since=N` · `POST /close/{ref}` |
| Token | `POIROT_TRACKER_URL` (default `http://127.0.0.1:8770`) + `POIROT_TRACKER_TOKEN` (Poirot) = `TRACKER_API_TOKEN` (tracker); the tracker listens on 127.0.0.1 only and refuses browser requests |
| Carries | carrier + B/L or AWB in; the parsed status, ETD/ETA, events out. A check younger than `max_age` (4 h) is answered at once, otherwise a live check and Poirot waits (300 s) |
| Owner | tracking events are the tracker's; what to do with them (3-day / 6-day thresholds, the shadow period) is Poirot's; every message to a party leaves from Poirot |
| Code | Poirot: `poirot/tracking/tracker_client.py`, `eta_check.py`, `carrier_rules.py` · Tracker: `api.py`, `tracking/poirot_api.py`, `tracking/service.py` |
| Resilience today | one live check at a time, a 60 s gap per carrier; a tracker that is down leaves the file tracked by what the forwarder wrote |
| Note | `GET /changes?since=N` already exists and is the natural base for stage 7 (scheduled checks that reach Poirot) |

### 4. Portal → every system: start, stop, probe, embed

| | |
|---|---|
| Direction | the portal runs each server from its own folder and `.venv`, probes its port, and embeds its pages in an `<iframe>` |
| Config | `systems.yaml` (servers, views, quick start, flow strip) |
| Probes | Poirot `/healthz` (expects `"heartbeat"`), RFQ `/openapi.json` (expects `"Kupfer API"`), tracker API `/health`, dashboard `/api/check-status` |
| Owner | nothing: the portal keeps only its own session and "recently opened" |
| Code | `portal/supervisor.py`, `portal/app.py` |
| Already covers | stage 3 of the plan (one place to start everything, live status per system, logs) |

## Sign-in today

| System | How |
|---|---|
| Portal | `KUPFER_PORTAL_KEY` in `.env` (stage 2); empty = no sign-in, as before |
| RFQ desk | `DESK_KEY` in `.env`; a signed cookie (`app/security.py`) - or the portal's `kupfer_session` (stage 2) |
| Poirot console | none (127.0.0.1 only); the customer page is a signed link (`POIROT_PORTAL_SECRET`) |
| Tracker | optional bearer token for programs; the dashboard has none |

Cookies are scoped by host name, not by port, so a cookie set by the portal on `localhost` is readable by the
other systems on `localhost:8000` / `:8001`. That is what stage 2 builds on, and why the README says to always
open the portal as `localhost`, never `127.0.0.1`.

## Connections to add (the plan's stages)

| Stage | Connection | Direction | Endpoint (proposed) | Owner | Notes |
|---|---|---|---|---|---|
| 2 ✔ | one session (built 29 Oct) | portal → RFQ desk | signed cookie `kupfer_session` on `localhost` (`portal/session.py`), verified by the desk with the shared `KUPFER_SESSION_SECRET` (`app/security.py`) | portal | the desk's `DESK_KEY` stays as a fallback; Poirot and the tracker have no sign-in today, so there was nothing to unify - the same cookie can lock them later |
| 4 ✔ | agent lookup (built 29 Oct) | Poirot → RFQ | `GET /api/agents?q=&mode=` · `GET /api/agents/{id}` (`agents.v1`, `app/routers/agents_api.py`, token `POIROT_EVENTS_TOKEN`) | RFQ | built on `app/agent_book/api.py`; read-only; no consumer yet (stage 6) |
| 4 ✔ | customer list (built 29 Oct) | RFQ → Poirot | `GET /api/clients/options?q=` · `GET /api/clients/{key}` (`clients.v1`, `poirot/web/app.py`, token `POIROT_INTAKE_TOKEN`) | Poirot | key + name for pick lists; read-only; no consumer yet (stage 6) |
| 4 ✔ | the numbers on the home page (built 29 Oct) | portal → RFQ, Poirot, tracker | `GET /api/summary` on the desk and on Poirot, `GET /summary` on the tracker API (`summary.v1`); the portal's `portal/summary.py`, tokens `KUPFER_RFQ_TOKEN` / `KUPFER_POIROT_TOKEN` / `KUPFER_TRACKER_TOKEN` | each | 8 s timeout (`KUPFER_SUMMARY_TIMEOUT`), 10 s cache; a system down shows "—" |
| 5 ✔ | automatic retry of the booking (built 30 Oct) | RFQ → Poirot | same `booking.v1`, sent again by `app/services/handoff_retry.py`: after 1, 5, 15, 60 min, then hourly, 24 attempts by itself; the desk rail lists what still waits; "Send again" always | – | only FAILED is retried (never REJECTED / NOT_SENT / SENT); Poirot's replay/409 answers make it safe |
| 7 | scheduled tracking that reaches the file | tracker → Poirot | tracker runs checks on a schedule; Poirot reads `GET /changes?since=N` (exists) or the tracker POSTs a signed `tracking.v1` event; per-file schedule set from Poirot; "check now" stays | tracker (events) / Poirot (what to do) | decided 29 Oct: build it (replaces the 8 Oct "live check only" rule); no message to a party ever leaves the tracker |

## The read contracts (stage 4)

Every answer carries `"contract"`; a key added, renamed or changed in meaning is a new version. Numbers are JSON
numbers, never strings. All GET, all read-only, all 401 on a bad token and 503 when the side has no token set.

**agents.v1** (RFQ desk, `Authorization: Bearer <POIROT_EVENTS_TOKEN>`)

- `GET /api/agents?q=<words>&mode=AIR|FCL|LCL` → `{contract, count, agents: [{id, name, country, city, status, modes, specialties}]}` - active agents, sorted by name; `modes` = the modes with an active service.
- `GET /api/agents/{id}` → `{contract, agent: {…the brief fields…, website, notes, always_cc, services: [{mode, active, covers, excludes, gateways}], contacts: [{id, name, role, email, phone, modes, is_primary, sales, paused}]}}` - the primary contact first; an archived agent is still readable (a booking may name it); unknown → 404.

**clients.v1** (Poirot, `Authorization: Bearer <POIROT_INTAKE_TOKEN>`)

- `GET /api/clients/options?q=<words>` → `{contract, count, clients: [{key, name, company, contact_name, email}]}` - sorted by name.
- `GET /api/clients/{key}` → `{contract, client: {key, name, company, contact_name, email, phone, address, tax_id, external_id, notes, source, files, updated_at}}`; unknown → 404. The key is Poirot's customer key (`ext:<quote-system id>`, the lower-cased email, or `name:<company>`).

**summary.v1**

- RFQ desk `GET /api/summary` → `{contract, system: "rfq", rfq_open, rfq_waiting, quotes_by_email, offers_awaiting_client, price_exposures, agents}`
- Poirot `GET /api/summary` → `{contract, system: "poirot", needs_you, files_open, on_the_way, arrived, bookings_pending, mail_to_file}`
- Tracker `GET /summary` (token `TRACKER_API_TOKEN` when set) → `{contract, system: "tracker", active, sea, air, stale, changes_24h, last_check_at}`

How the portal reads them: flow strip = `rfq_waiting`, `offers_awaiting_client`, `files_open`, `on_the_way`; "Needs you" = `needs_you`, `bookings_pending`, `rfq_waiting + quotes_by_email + price_exposures`, `stale`.

## Checked on 29 Oct 2026 (stage 0)

| Repo | Tests | Tag |
|---|---|---|
| RFQ desk | 665 passed, 3 skipped | `pre-unify` |
| Poirot | 2,846 passed, 26 skipped (after one test fix: `test_dropped_ack_does_not_swallow_the_next.py` – the dropped draft had no `created_at`) | `pre-unify` |
| Cargo tracker | 44 passed | `pre-unify` |
| Portal | 25 passed | `pre-unify` |
