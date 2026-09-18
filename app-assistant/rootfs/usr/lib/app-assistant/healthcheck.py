"""Health check for the container, asks the web server whether it answers."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8099/healthz", timeout=5) as answer:
        sys.exit(0 if answer.status == 200 else 1)
except (urllib.error.URLError, OSError):
    sys.exit(1)
