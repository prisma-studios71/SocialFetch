"""Settings read from environment variables."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from functools import lru_cache

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("socialfetch:" + APP_PASSWORD).encode()
).hexdigest()

# Max simultaneous downloads; the free hosting tier has ~512 MB RAM.
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "2"))


@lru_cache(maxsize=1)
def cookie_file() -> str | None:
    """Optional Netscape cookies.txt (e.g. a Render Secret File).

    yt-dlp writes cookies back to the file it was given, and secret files are
    read-only, so we work on a private copy.
    """
    src = os.environ.get("COOKIES_FILE")
    if not src or not os.path.isfile(src):
        return None
    fd, dst = tempfile.mkstemp(prefix="sf-cookies-", suffix=".txt")
    os.close(fd)
    shutil.copyfile(src, dst)
    return dst
