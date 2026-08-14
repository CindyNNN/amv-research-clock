"""Cross-platform JSON fetch used by Tencent / THS loaders.

Windows research machines historically called ``curl.exe``. GitHub Actions
runs Ubuntu, where the binary is ``curl``. urllib is the last fallback.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class HttpFetchError(RuntimeError):
    pass


def _curl_bin() -> str:
    return "curl.exe" if sys.platform == "win32" else "curl"


def curl_json(url: str, *, timeout: int = 60, attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    binary = _curl_bin()
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(
                [
                    binary,
                    "-sS",
                    "-L",
                    "--compressed",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    str(timeout),
                    "-A",
                    "Mozilla/5.0",
                    url,
                ],
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                errors="replace",
            )
            payload = json.loads(completed.stdout)
            if isinstance(payload, dict):
                return payload
            raise HttpFetchError(f"JSON object expected from {url}")
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            last_error = exc
            time.sleep(attempt)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            return payload
        raise HttpFetchError(f"JSON object expected from {url}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        last_error = exc
    raise HttpFetchError(f"failed to fetch {url}: {last_error}")
