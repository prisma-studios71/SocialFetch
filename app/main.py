from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import config
from .download import fetch_audio, fetch_item, fetch_zip
from .extract import UserError, resolve

log = logging.getLogger("socialfetch")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
AUTH_COOKIE = "sf_auth"

app = FastAPI(title="SocialFetch", docs_url=None, redoc_url=None, openapi_url=None)
_slots = threading.BoundedSemaphore(config.MAX_PARALLEL)

if not config.APP_PASSWORD:
    log.warning("APP_PASSWORD is not set - the site is open to anyone who finds it!")


# --------------------------------------------------------------------------- #
# Auth: one shared password -> signed cookie
# --------------------------------------------------------------------------- #

def _token() -> str:
    return hmac.new(config.SECRET_KEY.encode(), b"sf-auth-v1", hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    if not config.APP_PASSWORD:
        return True
    return hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _token())


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/me") and not _authed(request):
        return JSONResponse({"error": "Najpierw się zaloguj."}, status_code=401)
    response = await call_next(request)
    if path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    if not config.APP_PASSWORD or hmac.compare_digest(body.password, config.APP_PASSWORD):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            AUTH_COOKIE, _token(), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
            secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        )
        return resp
    time.sleep(1.5)  # slows down password guessing
    return JSONResponse({"error": "Złe hasło."}, status_code=401)


@app.get("/api/me")
def me(request: Request):
    return {"authed": _authed(request), "needs_password": bool(config.APP_PASSWORD)}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class InfoBody(BaseModel):
    url: str


@app.post("/api/info")
def info(body: InfoBody):
    try:
        return resolve(body.url).public()
    except UserError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/thumb")
def thumb(u: str, i: int = 0):
    """Preview images go through us: Instagram's CDN refuses to be embedded elsewhere.
    Only thumbnails of already resolved posts are served (no open proxy)."""
    import httpx

    try:
        post = resolve(u)
        url = post.items[i].thumb if 0 <= i < len(post.items) else None
    except UserError:
        url = None
    if not url:
        return Response(status_code=404)
    try:
        r = httpx.get(url, headers=post.headers, follow_redirects=True, timeout=20)
    except httpx.HTTPError:
        return Response(status_code=502)
    ctype = r.headers.get("content-type", "image/jpeg")
    if r.status_code != 200 or not ctype.startswith("image/"):
        return Response(status_code=502)
    return Response(r.content, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


def _error_page(message: str, status: int) -> HTMLResponse:
    from html import escape
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SocialFetch</title><style>body{{font-family:system-ui,sans-serif;background:#0d0d12;color:#eee;
display:grid;place-items:center;min-height:100vh;margin:0;padding:16px;text-align:center}}
a{{color:#b9a4ff}}</style><div><p style="font-size:2rem">😵</p><p>{escape(message)}</p>
<p><a href="/" onclick="history.length>1&&(history.back(),event.preventDefault())">← wróć</a></p></div>""",
        status_code=status,
    )


def _content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/api/download")
def download(u: str, mode: str = "video", i: int = 0, q: str | None = None, t: str | None = None):
    """mode: video (any single item) | audio | zip.  `t` is echoed back as a cookie
    so the page knows the browser started receiving the file."""
    if not _slots.acquire(timeout=60):
        return _error_page("Serwer jest zajęty, spróbuj za chwilę.", 429)
    workdir = tempfile.mkdtemp(prefix="sf-")
    try:
        post = resolve(u)
        if not 0 <= i < len(post.items):
            raise UserError("Nie ma takiego elementu.")
        if mode == "audio":
            path, name = fetch_audio(post, i, workdir)
        elif mode == "zip":
            path, name = fetch_zip(post, workdir)
        else:
            path, name = fetch_item(post, i, q, workdir)
    except UserError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return _error_page(str(e), 422)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        log.exception("download failed")
        return _error_page("Coś poszło nie tak przy pobieraniu.", 500)
    finally:
        _slots.release()

    resp = FileResponse(
        path,
        headers={"Content-Disposition": _content_disposition(name)},
        background=BackgroundTask(shutil.rmtree, workdir, True),
    )
    if t and re.fullmatch(r"[A-Za-z0-9]{1,32}", t):
        resp.set_cookie("sf_dl", t, max_age=120, samesite="lax")
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
