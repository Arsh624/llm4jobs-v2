"""Find the public job-board API (Greenhouse / Ashby / Lever) for a list of companies.

Two strategies per company:
  1. guess slugs from the name ("Physical Intelligence" -> physicalintelligence, physical-intelligence, ...)
  2. if a careers URL is given, download that page and look for embedded board links

Usage:
  python tools/discover.py companies.txt        # one per line: "Name" or "Name | careers-url"
Prints ready-to-paste [[source]] blocks for config/sources.toml, plus a list of misses.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
}
EMBED = {
    "greenhouse": re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
}
SKIP_SLUGS = {"embed", "js", "api", "v1", "jobs", "job_board"}


def _get(url: str, as_json: bool):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r) if as_json else r.read(800_000).decode("utf-8", "ignore")


def job_count(platform: str, slug: str) -> int:
    try:
        d = _get(API[platform].format(s=slug), as_json=True)
    except Exception:
        return 0
    if platform == "lever":
        return len(d) if isinstance(d, list) else 0
    return len(d.get("jobs", [])) if isinstance(d, dict) else 0


def slug_guesses(name: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|ltd|co|corp|technologies|company|labs?)\b\.?", " ", name.lower())
    words = re.findall(r"[a-z0-9]+", base)
    full = re.findall(r"[a-z0-9]+", name.lower())
    cands = ["".join(words), "-".join(words), "".join(full), "-".join(full)]
    if words and words[-1] != "ai":
        cands += ["".join(words) + "ai", "".join(words) + "-ai"]
    if words and words[-1] == "ai":
        cands.append("".join(words[:-1]))
    cands += ["".join(words) + "hq", "".join(words) + "inc"]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def discover(item: tuple[str, str | None]):
    name, url = item
    hits: dict[tuple[str, str], int] = {}
    if url:
        try:
            page = _get(url if url.startswith("http") else "https://" + url, as_json=False)
            for platform, rx in EMBED.items():
                for slug in set(rx.findall(page)):
                    if slug.lower() not in SKIP_SLUGS:
                        n = job_count(platform, slug)
                        if n:
                            hits[(platform, slug)] = n
        except Exception:
            pass
    if not hits:
        for slug in slug_guesses(name):
            for platform in API:
                n = job_count(platform, slug)
                if n:
                    hits[(platform, slug)] = n
            if hits:
                break
    return name, hits


def main(path: str) -> None:
    items = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, url = line.partition("|")
        items.append((name.strip(), url.strip() or None))
    found, missed = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for name, hits in ex.map(discover, items):
            if hits:
                (platform, slug), n = max(hits.items(), key=lambda kv: kv[1])
                found.append((name, platform, slug, n))
            else:
                missed.append(name)
    for name, platform, slug, n in found:
        print(f'[[source]]\ntype = "{platform}"\ncompany = "{name}"\nslug = "{slug}"   # {n} open jobs\n')
    print(f"# found {len(found)}, missed {len(missed)}: {', '.join(missed) or '-'}")


if __name__ == "__main__":
    main(sys.argv[1])
