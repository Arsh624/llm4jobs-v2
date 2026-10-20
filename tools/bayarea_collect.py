"""Collect company names from Wikipedia "Companies based in <city>, California" categories.

Uses the free MediaWiki API (structured, no scraping). Writes one name per line.
Usage: python tools/bayarea_collect.py > tools/lists/bayarea_raw.txt
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CITIES = [
    "San Francisco", "San Jose", "Santa Clara", "Sunnyvale", "Milpitas", "Cupertino",
    "Mountain View", "Palo Alto", "Menlo Park", "Redwood City", "San Mateo", "Fremont",
    "Foster City", "Los Gatos", "Campbell", "Oakland", "Emeryville", "South San Francisco",
    "Pleasanton", "San Carlos", "Burlingame", "Berkeley", "Newark", "Los Altos", "Belmont",
    "San Ramon", "Livermore", "Brisbane", "San Bruno", "Walnut Creek",
]
UA = {"User-Agent": "llm4jobs-v2/1.0 (personal job search tool)"}
SKIP = re.compile(r"^(List of|Category:|Template:|Portal:)", re.I)


SKIP_SUBCAT = re.compile(r"defunct|people|buildings|history|former|lists", re.I)


def _api(q: dict) -> dict:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(q)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                time.sleep(1.0)                      # be polite to Wikipedia
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


def members(category: str, cmtype: str = "page") -> list[str]:
    out, cont = [], None
    while True:
        q = {"action": "query", "list": "categorymembers", "cmtitle": category, "cmlimit": "500",
             "cmtype": cmtype, "format": "json"}
        if cont:
            q["cmcontinue"] = cont
        d = _api(q)
        out += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        cont = d.get("continue", {}).get("cmcontinue")
        if not cont:
            return out


def city_companies(city: str) -> list[str]:
    cat = f"Category:Companies based in {city}, California"
    pages = members(cat)
    for sub in members(cat, "subcat"):            # e.g. "Software companies based in San Francisco"
        if not SKIP_SUBCAT.search(sub):
            pages += members(sub)
    return pages


def clean(title: str) -> str:
    title = re.sub(r"\s*\((company|corporation|firm|software|website|American company|.*company.*)\)$", "", title)
    return title.strip()


if __name__ == "__main__":
    names = {}
    for city in CITIES:
        try:
            for t in city_companies(city):
                if not SKIP.search(t):
                    names.setdefault(clean(t), city)
        except Exception as e:
            print(f"# {city}: {e}", file=sys.stderr)
    for n, city in sorted(names.items()):
        print(f"{n}\t{city}")
    print(f"# {len(names)} companies", file=sys.stderr)
