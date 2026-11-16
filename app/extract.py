"""Turning a pasted link into a list of downloadable items.

Engines:
  * TikTok video   -> yt-dlp (the API already serves watermark-free streams)
  * TikTok photos  -> gallery-dl (yt-dlp does not support photo posts)
  * Instagram      -> yt-dlp; photo items come back without formats, but the
                      full-size image is still listed among the thumbnails
  * X / Twitter    -> FxTwitter public API (photos + videos), yt-dlp fallback
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import yt_dlp

from . import config

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

PLATFORM_HOSTS = {
    "instagram": ("instagram.com", "instagr.am", "ddinstagram.com"),
    "tiktok": ("tiktok.com",),
    "x": ("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com"),
}

REFERERS = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/",
    "x": "https://x.com/",
}


class UserError(Exception):
    """An error whose message is safe and meaningful to show in the UI."""


@dataclass
class Quality:
    id: str
    label: str


@dataclass
class Item:
    kind: str  # "video" | "image" | "audio"
    source: str  # "ytdlp" | "direct"
    thumb: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    ext: str = "mp4"
    qualities: list[Quality] = field(default_factory=list)
    # internal, never sent to the browser
    info: dict | None = None  # yt-dlp entry (source == "ytdlp")
    urls: dict[str, str] = field(default_factory=dict)  # quality id -> url (source == "direct")
    selectors: dict[str, str] = field(default_factory=dict)  # quality id -> yt-dlp format selector

    def public(self) -> dict:
        return {
            "kind": self.kind,
            "thumb": self.thumb,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "qualities": [q.__dict__ for q in self.qualities],
        }


@dataclass
class Post:
    platform: str
    url: str
    id: str
    author: str | None
    caption: str | None
    thumb: str | None
    items: list[Item]
    audio: Item | None = None  # standalone sound (e.g. TikTok photo slideshow)
    created: float = field(default_factory=time.time)

    @property
    def headers(self) -> dict:
        return {"User-Agent": UA, "Referer": REFERERS[self.platform]}

    def public(self) -> dict:
        return {
            "platform": self.platform,
            "url": self.url,
            "id": self.id,
            "author": self.author,
            "caption": self.caption,
            "thumb": self.thumb,
            "items": [i.public() for i in self.items],
            "has_audio": self.audio is not None or any(i.kind == "video" for i in self.items),
        }


# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #

def platform_of(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for name, hosts in PLATFORM_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return None


def normalize(text: str) -> tuple[str, str]:
    """Pull the first supported link out of whatever got pasted."""
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(").,;!?")
        platform = platform_of(url)
        if platform:
            return platform, url
    raise UserError("Wklej link z Instagrama, TikToka albo X.")


def _resolve_short_tiktok(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.split(".")[0] not in ("vm", "vt") and "/t/" not in url:
        return url
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=15)
        return str(r.url)
    except httpx.HTTPError:
        return url


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #

def ytdlp_params(**extra) -> dict:
    params = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignore_no_formats_error": True,
        "socket_timeout": 20,
        "retries": 2,
    }
    cookies = config.cookie_file()
    if cookies:
        params["cookiefile"] = cookies
    params.update(extra)
    return params


def _short_side(f: dict) -> int | None:
    w, h = f.get("width"), f.get("height")
    if w and h:
        return min(w, h)
    return h or w


def _is_watermarked(f: dict) -> bool:
    note = f"{f.get('format_note') or ''} {f.get('format') or ''}".lower()
    return "watermark" in note or f.get("format_id") == "download"


def _video_qualities(entry: dict) -> tuple[list[Quality], dict[str, str]]:
    best_per_res: dict[int, dict] = {}
    for f in entry.get("formats") or []:
        if f.get("vcodec") == "none" or _is_watermarked(f):
            continue
        res = _short_side(f)
        if not res:
            continue
        vcodec = (f.get("vcodec") or "").lower()
        score = (
            f.get("acodec") != "none",  # already has sound -> no merge needed
            vcodec.startswith(("avc", "h264")) or vcodec == "",  # plays everywhere
            not (f.get("protocol") or "").startswith("m3u8"),
            f.get("tbr") or 0,
        )
        cur = best_per_res.get(res)
        if cur is None or score > cur["_score"]:
            best_per_res[res] = {**f, "_score": score}

    def is_hevc(f: dict) -> bool:
        return (f.get("vcodec") or "").lower().startswith(("hev", "hvc", "h265", "bytevc1"))

    # H.264 first (plays everywhere), HEVC after it; highest resolution first within each.
    ordered = sorted(best_per_res.items(), key=lambda kv: (is_hevc(kv[1]), -kv[0]))
    qualities, selectors = [], {}
    for res, f in ordered:
        fid = f["format_id"]
        qid = f"r{res}"
        qualities.append(Quality(qid, f"{res}p" + (" (HEVC)" if is_hevc(f) else "")))
        # Only trust a stream to carry sound when an audio codec is actually named;
        # Instagram's progressive files are often video-only despite "has_audio".
        acodec = f.get("acodec")
        selectors[qid] = fid if acodec and acodec != "none" else f"{fid}+bestaudio/{fid}"

    if not qualities:
        qualities.append(Quality("best", "Najlepsza"))
        selectors["best"] = "bv*[format_note!*=watermark]+ba/b[format_note!*=watermark]/b"
    return qualities, selectors


_IG_SIZE_RE = re.compile(r"[_/]s(\d+)x(\d+)")


def _best_image(entry: dict) -> str | None:
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return entry.get("thumbnail")
    # Instagram: the original has no "sNNNxNNN" resize marker in its URL.
    unsized = [t for t in thumbs if not _IG_SIZE_RE.search(t["url"])]
    if unsized:
        return unsized[-1]["url"]
    return max(thumbs, key=lambda t: (t.get("width") or 0, t.get("preference") or 0))["url"]


def _item_from_entry(entry: dict) -> Item:
    has_video = any(f.get("vcodec") != "none" for f in entry.get("formats") or [])
    if not has_video:
        img = _best_image(entry)
        if not img:
            raise UserError("Nie znalazłem w tym poście nic do pobrania.")
        return Item(kind="image", source="direct", thumb=img, ext="jpg",
                    qualities=[Quality("orig", "Oryginał")], urls={"orig": img})
    qualities, selectors = _video_qualities(entry)
    return Item(
        kind="video",
        source="ytdlp",
        thumb=entry.get("thumbnail"),
        width=entry.get("width"),
        height=entry.get("height"),
        duration=entry.get("duration"),
        qualities=qualities,
        selectors=selectors,
        info=entry,
    )


def _via_ytdlp(platform: str, url: str) -> Post:
    with yt_dlp.YoutubeDL(ytdlp_params()) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    entries = [e for e in (info.get("entries") or [info]) if e]
    items = [_item_from_entry(copy.deepcopy(e)) for e in entries]
    if not items:
        raise UserError("Nie znalazłem w tym poście nic do pobrania.")
    first = entries[0]
    return Post(
        platform=platform,
        url=url,
        id=str(info.get("id") or first.get("id")),
        author=(info.get("uploader") if platform == "tiktok" else None)
        or info.get("channel") or info.get("uploader_id") or info.get("uploader"),
        caption=info.get("description") or first.get("description") or info.get("title"),
        thumb=items[0].thumb,
        items=items,
    )


# --------------------------------------------------------------------------- #
# gallery-dl (TikTok photo posts)
# --------------------------------------------------------------------------- #

def _via_gallerydl(platform: str, url: str) -> Post:
    cmd = [sys.executable, "-m", "gallery_dl", "-j"]
    cookies = config.cookie_file()
    if cookies:
        cmd += ["--cookies", cookies]
    try:
        proc = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=60)
        messages = json.loads(proc.stdout or "[]")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise UserError("Nie udało się odczytać posta.") from e

    meta: dict = {}
    items: list[Item] = []
    audio: Item | None = None
    for msg in messages:
        if msg[0] == -1:
            raise RuntimeError(msg[1].get("message", "gallery-dl error"))
        if msg[0] == 2:
            meta = msg[1]
        elif msg[0] == 3:
            file_url, kw = msg[1], msg[2]
            ext = kw.get("extension") or "jpg"
            if kw.get("type") == "audio" or ext in ("mp3", "m4a"):
                audio = Item(kind="audio", source="direct", ext=ext,
                             qualities=[Quality("orig", "Oryginał")], urls={"orig": file_url})
            elif ext in ("mp4", "mov", "webm"):
                items.append(Item(kind="video", source="direct", ext=ext,
                                  qualities=[Quality("orig", "Oryginał")], urls={"orig": file_url}))
            else:
                items.append(Item(kind="image", source="direct", ext=ext, thumb=file_url,
                                  width=kw.get("width"), height=kw.get("height"),
                                  qualities=[Quality("orig", "Oryginał")], urls={"orig": file_url}))
    if not items:
        raise UserError("Nie znalazłem w tym poście nic do pobrania.")
    author = meta.get("user") or meta.get("username")
    if isinstance(meta.get("author"), dict):
        author = author or meta["author"].get("uniqueId")
    return Post(platform=platform, url=url, id=str(meta.get("id") or ""), author=author,
                caption=meta.get("desc") or meta.get("description"), thumb=items[0].thumb,
                items=items, audio=audio)


# --------------------------------------------------------------------------- #
# FxTwitter (X)
# --------------------------------------------------------------------------- #

_STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")
_RES_RE = re.compile(r"/(\d+)x(\d+)/")


def _via_fxtwitter(url: str) -> Post:
    m = _STATUS_RE.search(url)
    if not m:
        raise UserError("To nie wygląda na link do posta na X.")
    status_id = m.group(1)
    r = httpx.get(f"https://api.fxtwitter.com/status/{status_id}",
                  headers={"User-Agent": "SocialFetch/1.0"}, timeout=20)
    data = r.json()
    if data.get("code") != 200 or "tweet" not in data:
        raise RuntimeError(data.get("message") or f"fxtwitter {r.status_code}")
    tweet = data["tweet"]
    items: list[Item] = []
    for media in (tweet.get("media") or {}).get("all") or []:
        mtype = media.get("type")
        if mtype == "photo":
            if "/card_img/" in media.get("url", ""):
                continue
            img = media["url"]
            orig = img if "name=" in img else (img + ("&" if "?" in img else "?") + "name=orig")
            items.append(Item(kind="image", source="direct", ext="jpg", thumb=img,
                              width=media.get("width"), height=media.get("height"),
                              qualities=[Quality("orig", "Oryginał")], urls={"orig": orig}))
        elif mtype in ("video", "gif"):
            variants: dict[int, str] = {}
            for v in media.get("variants") or []:
                if v.get("content_type") != "video/mp4":
                    continue
                rm = _RES_RE.search(v["url"])
                res = min(int(rm.group(1)), int(rm.group(2))) if rm else 0
                variants.setdefault(res, v["url"])
            if media.get("url") and media.get("format", "video/mp4") == "video/mp4":
                w, h = int(media.get("width") or 0), int(media.get("height") or 0)
                variants.setdefault(min(w, h) if w and h else 0, media["url"])
            ordered = sorted(variants.items(), key=lambda kv: kv[0], reverse=True)
            if not ordered:
                continue
            qualities = [Quality(f"r{res}", f"{res}p" if res else "Najlepsza") for res, _ in ordered]
            items.append(Item(kind="video", source="direct", ext="mp4",
                              thumb=media.get("thumbnail_url"),
                              width=media.get("width"), height=media.get("height"),
                              duration=float(media["duration"]) if media.get("duration") else None,
                              qualities=qualities,
                              urls={f"r{res}": u for res, u in ordered}))
    if not items:
        raise UserError("W tym poście nie ma zdjęć ani filmów.")
    return Post(platform="x", url=url, id=status_id,
                author=(tweet.get("author") or {}).get("screen_name"),
                caption=tweet.get("text"), thumb=items[0].thumb, items=items)


# --------------------------------------------------------------------------- #
# Public entry point + small TTL cache
# --------------------------------------------------------------------------- #

_cache: dict[str, Post] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 15 * 60


def _friendly(err: Exception) -> UserError:
    msg = str(err).lower()
    if "login" in msg or "rate-limit" in msg or "registered users" in msg:
        return UserError("Platforma wymaga zalogowania albo przyblokowała serwer. "
                         "Spróbuj za chwilę (albo dodaj cookies, patrz README).")
    if "private" in msg:
        return UserError("Ten post jest prywatny.")
    if "unavailable" in msg or "not available" in msg or "404" in msg or "not found" in msg:
        return UserError("Post nie istnieje albo został usunięty.")
    if "unsupported url" in msg:
        return UserError("Tego typu linku nie obsługuję.")
    return UserError("Nie udało się pobrać posta. Sprawdź link i spróbuj ponownie.")


def resolve(text: str) -> Post:
    platform, url = normalize(text)

    with _cache_lock:
        now = time.time()
        for key in [k for k, p in _cache.items() if now - p.created > CACHE_TTL]:
            _cache.pop(key, None)
        if url in _cache:
            return _cache[url]

    try:
        if platform == "x":
            try:
                post = _via_fxtwitter(url)
            except UserError:
                raise
            except Exception:
                post = _via_ytdlp(platform, url)
        elif platform == "tiktok":
            url = _resolve_short_tiktok(url)
            if "/photo/" in url:
                post = _via_gallerydl(platform, url)
            else:
                try:
                    post = _via_ytdlp(platform, url)
                    if not any(i.kind == "video" for i in post.items):
                        raise RuntimeError("no video")
                except Exception:  # photo slideshows etc. -> gallery-dl
                    post = _via_gallerydl(platform, url)
        else:
            post = _via_ytdlp(platform, url)
    except UserError:
        raise
    except Exception as e:  # noqa: BLE001 - every engine failure becomes a friendly message
        raise _friendly(e) from e

    with _cache_lock:
        _cache[normalize(text)[1]] = post
        _cache[url] = post
        while len(_cache) > 100:
            _cache.pop(next(iter(_cache)))
    return post
