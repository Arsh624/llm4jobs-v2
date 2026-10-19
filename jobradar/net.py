"""Tiny stdlib HTTP client with retries and backoff (no third-party deps)."""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.request

LOG = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; llm4jobs-v2/1.0)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def request_json(url: str, body: dict | None = None, *, headers: dict | None = None,
                 timeout: float = 30, retries: int = 4):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == retries:
                raise
            wait = _retry_after(e) or (2 ** attempt) + random.random()
            LOG.debug("http %s on %s, retry in %.1fs", e.code, url, wait)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise
            time.sleep((2 ** attempt) + random.random())
            LOG.debug("network error %s on %s, retrying", e, url)


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    v = e.headers.get("Retry-After") if e.headers else None
    try:
        return min(float(v), 30.0) if v else None
    except ValueError:
        return None
