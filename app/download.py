"""Fetching media to a temp dir so it can be handed to the browser."""

from __future__ import annotations

import copy
import glob
import os
import re
import subprocess
import zipfile

import httpx
import yt_dlp

from .extract import Item, Post, UserError, ytdlp_params


def _safe(text: str | None, limit: int = 40) -> str:
    text = re.sub(r"[^\w\-]+", "_", text or "", flags=re.UNICODE).strip("_")
    return text[:limit] or "media"


def base_name(post: Post, index: int | None = None) -> str:
    parts = [post.platform, _safe(post.author, 30), _safe(post.id, 30)]
    if index is not None and len(post.items) > 1:
        parts.append(str(index + 1))
    return "_".join(parts)


def _fetch_direct(post: Post, url: str, dest: str) -> str:
    with httpx.stream("GET", url, headers=post.headers, follow_redirects=True, timeout=60) as r:
        if r.status_code >= 400:
            raise UserError("Serwer platformy odrzucił pobieranie. Odśwież i spróbuj ponownie.")
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
    return dest


def _ytdlp_download(item: Item, selector: str, workdir: str) -> str:
    params = ytdlp_params(
        format=selector,
        outtmpl=os.path.join(workdir, "media.%(ext)s"),
        merge_output_format="mp4",
        noplaylist=True,
        overwrites=True,
    )
    with yt_dlp.YoutubeDL(params) as ydl:
        # Re-use the already extracted info: no second round-trip to the platform.
        ydl.process_ie_result(copy.deepcopy(item.info), download=True)
    files = [f for f in glob.glob(os.path.join(workdir, "media.*")) if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise UserError("Pobieranie się nie udało.")
    return max(files, key=os.path.getsize)


def _pick_quality(item: Item, quality: str | None) -> str:
    ids = [q.id for q in item.qualities]
    return quality if quality in ids else ids[0]


def fetch_item(post: Post, index: int, quality: str | None, workdir: str) -> tuple[str, str]:
    """Download one item. Returns (local path, suggested filename)."""
    item = post.items[index]
    qid = _pick_quality(item, quality)
    if item.source == "ytdlp":
        path = _ytdlp_download(item, item.selectors[qid], workdir)
    else:
        path = _fetch_direct(post, item.urls[qid], os.path.join(workdir, f"media.{item.ext}"))
    ext = os.path.splitext(path)[1] or f".{item.ext}"
    return path, base_name(post, index) + ext


def _to_mp3(src: str, dest: str) -> str:
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
         "-of", "csv=p=0", src], capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    if not codec:
        raise UserError("Ten film nie ma ścieżki dźwiękowej.")
    if codec == "mp3" and src.endswith(".mp3"):
        return src
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-c:a", "libmp3lame", "-q:a", "2", dest],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0 or not os.path.exists(dest):
        raise UserError("Ten film nie ma ścieżki dźwiękowej.")
    return dest


def fetch_audio(post: Post, index: int, workdir: str) -> tuple[str, str]:
    name = base_name(post, index if post.audio is None else None) + ".mp3"
    if post.audio is not None:  # slideshow sound
        src = _fetch_direct(post, post.audio.urls["orig"], os.path.join(workdir, f"sound.{post.audio.ext}"))
        return _to_mp3(src, os.path.join(workdir, "audio.mp3")), name

    item = post.items[index]
    if item.kind != "video":
        raise UserError("Ten element nie ma dźwięku.")
    if item.source == "ytdlp":
        src = _ytdlp_download(item, "ba/b", workdir)
    else:  # direct mp4: the smallest variant has the same audio track
        src = _fetch_direct(post, item.urls[item.qualities[-1].id], os.path.join(workdir, "src.mp4"))
    return _to_mp3(src, os.path.join(workdir, "audio.mp3")), name


def fetch_zip(post: Post, workdir: str) -> tuple[str, str]:
    zip_path = os.path.join(workdir, "bundle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for i in range(len(post.items)):
            sub = os.path.join(workdir, f"item{i}")
            os.makedirs(sub)
            path, name = fetch_item(post, i, None, sub)
            zf.write(path, name)
            os.remove(path)
        if post.audio is not None:
            sub = os.path.join(workdir, "sound")
            os.makedirs(sub)
            path, name = fetch_audio(post, 0, sub)
            zf.write(path, name)
    return zip_path, base_name(post) + ".zip"
