"""Kupfer portal - one page to open, start and move between the systems.

Starlette (the engine FastAPI is built on) + Jinja + HTMX. Listens on this computer only (127.0.0.1).
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote as urlquote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from portal import i18n
from portal.config import Config, View, load, safe_path
from portal.recent import Recent
from portal.session import COOKIE as SESSION_COOKIE, Session
from portal.summary import Summaries, home_numbers
from portal.supervisor import CONFLICT, CRASHED, EXTERNAL, RUNNING, STARTING, Status, Supervisor

HERE = Path(__file__).resolve().parent
LOCAL_HOSTS = ("localhost", "127.0.0.1")
DESK_COOKIE = "roark_desk"      # the RFQ desk's own sign-in cookie: signing out of the portal clears it too
OPEN_PATHS = ("/login", "/healthz")   # reachable without a session (and /static)


def create_app(config: Optional[Config] = None, supervisor: Optional[Supervisor] = None,
               recent: Optional[Recent] = None, stop_on_exit: bool = True,
               session: Optional[Session] = None, summaries: Optional[Summaries] = None) -> Starlette:
    cfg = config or load()
    sup = supervisor or Supervisor(cfg.servers)
    rec = recent or Recent()
    ses = session if session is not None else Session.from_env()
    sums = summaries if summaries is not None else Summaries.from_config(cfg.servers)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    asset_v = str(int(time.time()))

    # ------------------------------------------------------------------ helpers
    def ctx(request: Request, **extra) -> dict:
        lang = i18n.pick(request.cookies.get(i18n.COOKIE))
        return {"request": request, "lang": lang, "t": i18n.translator(lang), "cfg": cfg,
                "dir": "ltr" if lang == "en" else "rtl", "v": asset_v, "session_on": ses.on, **extra}

    def render(request: Request, name: str, status_code: int = 200, **extra) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx(request, **extra), status_code=status_code)

    def system_url(request: Request, view: View, path: str) -> str:
        host = request.url.hostname if request.url.hostname in LOCAL_HOSTS else "localhost"
        # the same host name as the portal, so each system's sign-in cookie still counts inside the tab
        return f"http://{host}:{cfg.servers[view.server].port}{path}"

    def statuses() -> dict[str, Status]:
        return sup.all()

    def status_ctx(request: Request) -> dict:
        st = statuses()
        lang = i18n.pick(request.cookies.get(i18n.COOKIE))
        return {"st": st, "up": sum(1 for s in st.values() if s.up), "total": len(st),
                "uptime": {sid: i18n.uptime(s.since, lang) for sid, s in st.items()},
                "logs": {sid: sup.log_path(sid).exists() for sid in cfg.servers},
                "owned": {sid: sup.owned(sid) for sid in cfg.servers}}

    # ------------------------------------------------------------------ pages
    async def home(request: Request) -> Response:
        data = await run_in_threadpool(status_ctx, request)
        return render(request, "home.html", **data)

    async def set_lang(request: Request) -> Response:
        to = request.query_params.get("to", "he")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(i18n.COOKIE, to if to in i18n.LANGS else "he", max_age=365 * 24 * 3600, samesite="lax")
        return resp

    async def status_partial(request: Request) -> Response:
        data = await run_in_threadpool(status_ctx, request)
        return render(request, "partials/status.html", **data)

    async def recent_partial(request: Request) -> Response:
        today = time.strftime("%Y-%m-%d")
        items = []
        for i in rec.items():
            if i["view"] not in cfg.views:
                continue
            at = time.localtime(i.get("at") or 0)
            when = time.strftime("%H:%M" if time.strftime("%Y-%m-%d", at) == today else "%d.%m", at)
            items.append({**i, "when": when})
        return render(request, "partials/recent.html", items=items)

    async def flow_partial(request: Request) -> Response:
        """The numbers on the flow strip and the 'Needs you' card - each system's summary.v1 (portal/summary.py)."""
        numbers = await run_in_threadpool(lambda: home_numbers(sums.all()))
        name = "partials/needs.html" if request.url.path.endswith("/needs") else "partials/flow.html"
        return render(request, name, n=numbers)

    async def tab_partial(request: Request, view_id: Optional[str] = None, path: Optional[str] = None,
                          remember: bool = True) -> Response:
        view_id = view_id or request.path_params["view"]
        view = cfg.views.get(view_id)
        if view is None:
            return PlainTextResponse("unknown view", status_code=404)
        if view.external:
            return render(request, "partials/tab.html", view=view, state="link")
        path = safe_path(path or request.query_params.get("path") or view.path)
        server = cfg.servers[view.server]
        st = await run_in_threadpool(sup.status, server.id)
        if st.up:
            if remember:
                rec.add(view.id, path)
            return render(request, "partials/tab.html", view=view, server=server, state="frame",
                          src=system_url(request, view, path), path=path)
        return render(request, "partials/tab.html", view=view, server=server, state=st.state, status=st,
                      path=path, has_log=sup.log_path(server.id).exists())

    async def server_action(request: Request) -> Response:
        sid, action = request.path_params["sid"], request.path_params["action"]
        if sid not in cfg.servers or action not in ("start", "stop", "restart"):
            return PlainTextResponse("not found", status_code=404)
        await run_in_threadpool(getattr(sup, action), sid)
        view_id = request.query_params.get("view")
        if view_id:   # pressed inside a tab: answer with the tab
            return await tab_partial(request, view_id=view_id, path=request.query_params.get("path"), remember=False)
        return await status_partial(request)

    async def all_action(request: Request) -> Response:
        action = request.path_params["action"]
        if action == "start":
            for sid in cfg.servers:
                await run_in_threadpool(sup.start, sid)
        elif action == "stop":
            await run_in_threadpool(sup.stop_all)
        else:
            return PlainTextResponse("not found", status_code=404)
        return await status_partial(request)

    async def log_page(request: Request) -> Response:
        sid = request.path_params["sid"]
        if sid not in cfg.servers:
            return PlainTextResponse("not found", status_code=404)
        text = await run_in_threadpool(sup.tail, sid)
        name = "partials/log_body.html" if request.headers.get("HX-Request") else "log.html"
        return render(request, name, server=cfg.servers[sid], text=text)

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    # ------------------------------------------------------------------ the one sign-in (portal/session.py)
    def signed_in(request: Request) -> bool:
        return (not ses.on) or ses.signed_in(request.cookies.get(SESSION_COOKIE))

    async def login_page(request: Request) -> Response:
        if not ses.on:
            return RedirectResponse("/", status_code=303)
        nxt = safe_path(request.query_params.get("next"))
        if signed_in(request):
            return RedirectResponse(nxt, status_code=303)
        return render(request, "login.html", next=nxt, error=bool(request.query_params.get("error")))

    async def login(request: Request) -> Response:
        if not ses.on:
            return RedirectResponse("/", status_code=303)
        form = await request.form()
        nxt = safe_path(str(form.get("next") or "/"))
        if not ses.key_matches(str(form.get("key") or "")):
            return RedirectResponse(f"/login?error=1&next={urlquote(nxt, safe='/')}", status_code=303)
        resp = RedirectResponse(nxt, status_code=303)
        # No "domain": a host-only cookie on localhost, which the systems on the other ports receive as well.
        resp.set_cookie(SESSION_COOKIE, ses.cookie_value(), max_age=ses.max_age, httponly=True, samesite="lax", path="/")
        return resp

    async def logout(request: Request) -> Response:
        resp = RedirectResponse("/login" if ses.on else "/", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        resp.delete_cookie(DESK_COOKIE, path="/")      # out of the portal = out of the RFQ desk too
        return resp

    # ------------------------------------------------------------------ the guard
    class LocalOnly(BaseHTTPMiddleware):
        """Only this computer, and a POST only from the portal's own page (another site cannot start or stop
        anything by sending your browser here)."""

        async def dispatch(self, request: Request, call_next):
            host = (request.headers.get("host") or "").lower()
            allowed = {f"{h}:{cfg.port}" for h in LOCAL_HOSTS}
            if host not in allowed:
                return PlainTextResponse("forbidden host", status_code=403)
            if request.method == "POST":
                origin = (request.headers.get("origin") or "").lower()
                if origin not in {f"http://{h}" for h in allowed}:
                    return PlainTextResponse("forbidden origin", status_code=403)
            path = request.url.path
            if ses.on and path not in OPEN_PATHS and not path.startswith("/static/") and not signed_in(request):
                if request.headers.get("HX-Request"):      # a partial: send the page to the sign-in, not the fragment
                    return Response(status_code=401, headers={"HX-Redirect": "/login"})
                if request.method != "GET":
                    return PlainTextResponse("sign in first", status_code=401)
                return RedirectResponse(f"/login?next={urlquote(path, safe='/')}", status_code=303)
            return await call_next(request)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        yield
        if stop_on_exit:
            await run_in_threadpool(sup.stop_all)   # what the portal started stops with it

    routes = [
        Route("/", home),
        Route("/lang", set_lang),
        Route("/healthz", healthz),
        Route("/login", login_page),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/partials/status", status_partial),
        Route("/partials/recent", recent_partial),
        Route("/partials/flow", flow_partial),
        Route("/partials/needs", flow_partial),
        Route("/partials/tab/{view}", tab_partial),
        Route("/servers/all/{action}", all_action, methods=["POST"]),
        Route("/servers/{sid}/log", log_page),
        Route("/servers/{sid}/{action}", server_action, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(LocalOnly)], lifespan=lifespan)
    app.state.supervisor = sup
    app.state.config = cfg
    app.state.session = ses
    app.state.summaries = sums

    templates.env.globals.update(RUNNING=RUNNING, EXTERNAL=EXTERNAL, STARTING=STARTING, CRASHED=CRASHED,
                                 CONFLICT=CONFLICT)
    return app
