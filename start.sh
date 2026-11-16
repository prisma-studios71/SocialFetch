#!/bin/sh
set -e
# Platforms change their sites all the time; fresh extractors fix most breakages.
# The free Render instance restarts after sleeping, so this keeps us current.
if [ "${AUTO_UPDATE:-1}" = "1" ]; then
  timeout 60 pip install --no-cache-dir -q -U yt-dlp gallery-dl || echo "extractor update skipped"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
