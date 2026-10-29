"""Reliability audit: for every enabled source, fetch its feed and open one real job link.

Usage: python tools/audit.py            -> prints problems + summary, writes out/audit.json
A source is OK when the feed returns jobs and a sample job URL loads (HTTP < 400).
Age-aware feeds (Workday, Jibe, Eightfold, Oracle) only return recent jobs, so 0 jobs there
is reported as "quiet" rather than broken.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jobradar.pipeline import DEFAULTS, _fetch_one, load_config  # noqa: E402
from jobradar.sources import AGE_AWARE  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept": "text/html"}


def link_ok(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
            return r.status < 400, str(r.status)
    except urllib.error.HTTPError as e:
        # 403/429 = bot protection on a real page (a person's browser opens it fine)
        return e.code in (403, 429), str(e.code)
    except Exception as e:
        return False, type(e).__name__


def audit(src: dict) -> dict:
    label = src.get("company", src["type"])
    # Same code path as a real run (incl. job_url_template rewrites), with a 7-day window.
    _, jobs, err = _fetch_one(src, {**DEFAULTS, "max_age_days": 7})
    if err:
        return {"source": label, "type": src["type"], "status": "FEED_ERROR", "detail": err[:90]}
    if not jobs:
        status = "QUIET" if src["type"] in AGE_AWARE else "EMPTY"
        return {"source": label, "type": src["type"], "status": status, "jobs": 0}
    sample = next((j for j in jobs if j.url), None)
    if sample is None:
        return {"source": label, "type": src["type"], "status": "NO_LINKS", "jobs": len(jobs)}
    ok, code = link_ok(sample.url)
    return {"source": label, "type": src["type"], "status": "OK" if ok else "BAD_LINK", "jobs": len(jobs),
            "sample": sample.url, "http": code}


def main() -> None:
    _, sources = load_config(ROOT / "config" / "sources.toml")
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(audit, sources))
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "audit.json").write_text(json.dumps(results, indent=1), "utf-8")
    from collections import Counter
    c = Counter(r["status"] for r in results)
    for r in sorted(results, key=lambda r: r["status"]):
        if r["status"] != "OK":
            print(f"{r['status']:10} {r['source'][:32]:32} {r['type']:15} {r.get('detail') or r.get('sample', '')[:80]} {r.get('http', '')}")
    print(f"\n{len(results)} sources: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))


if __name__ == "__main__":
    main()
