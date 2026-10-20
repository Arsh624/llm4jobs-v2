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
FAST = "--fast" in sys.argv   # bulk mode: only the 2 most likely slugs per company


def _get(url: str, as_json: bool):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r) if as_json else r.read(800_000).decode("utf-8", "ignore")


def _board(platform: str, slug: str):
    try:
        return _get(API[platform].format(s=slug), as_json=True)
    except Exception:
        return None


def _jobs(platform: str, d) -> list:
    if platform == "lever":
        return d if isinstance(d, list) else []
    return d.get("jobs", []) if isinstance(d, dict) else []


def job_count(platform: str, slug: str) -> int:
    return len(_jobs(platform, _board(platform, slug)))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def belongs_to(name: str, platform: str, slug: str) -> bool:
    """Guard against same-slug, different-company boards (e.g. 'fleet' = Fleet Data Centers)."""
    key = _norm(re.sub(r"\b(inc|llc|corp|corporation|company|technologies|technology)\b\.?", "", name, flags=re.I))
    words = key.split()
    # Match the full name; fall back to the first word only if it is distinctive (>= 6 chars),
    # so "Fleet AI" can't match "Fleet Data Centers" via the word "fleet".
    probes = [key] + ([words[0]] if len(words) > 1 and len(words[0]) >= 6 else [])
    if platform == "greenhouse":
        try:
            board_name = _norm(_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", as_json=True).get("name", ""))
            if key and (key in board_name or board_name in key):
                return True
        except Exception:
            pass
        try:
            jobs = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", as_json=True).get("jobs", [])[:5]
            texts = [j.get("content", "") for j in jobs]
        except Exception:
            return False
    else:
        jobs = _jobs(platform, _board(platform, slug))[:5]
        texts = [(j.get("descriptionPlain") or j.get("descriptionHtml") or "") for j in jobs]
    blob = f" {_norm(' '.join(texts))} "
    return any(p and f" {p} " in blob for p in probes)


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
        guesses = slug_guesses(name)
        for slug in (guesses[:2] if FAST else guesses):
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
    found, unverified, missed = [], [], []

    def run(item):
        name, hits = discover(item)
        if not hits:
            return name, None, False
        (platform, slug), n = max(hits.items(), key=lambda kv: kv[1])
        return name, (platform, slug, n), belongs_to(name, platform, slug)

    with ThreadPoolExecutor(max_workers=10) as ex:
        for name, hit, ok in ex.map(run, items):
            if not hit:
                missed.append(name)
            elif ok:
                found.append((name, *hit))
            else:
                unverified.append((name, *hit))
    for name, platform, slug, n in found:
        print(f'[[source]]\ntype = "{platform}"\ncompany = "{name}"\nslug = "{slug}"   # {n} open jobs\n')
    for name, platform, slug, n in unverified:
        print(f"# UNVERIFIED (board may belong to another company): {name} -> {platform}:{slug} ({n} jobs)")
    print(f"# found {len(found)}, unverified {len(unverified)}, missed {len(missed)}")
    if "-v" in sys.argv:
        print(f"# missed: {', '.join(missed)}")


if __name__ == "__main__":
    main(sys.argv[1])
