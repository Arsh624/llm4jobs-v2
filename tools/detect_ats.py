"""Detect a company's job board from its careers page, then confirm it with a live fetch.

Input lines: "Company | domain-or-careers-url[, more urls]"
Output: ready-to-paste [[source]] blocks for boards that returned jobs, plus misses.

Usage: python tools/detect_ats.py tools/lists/midtier.txt
"""
from __future__ import annotations

import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobradar.sources import ADAPTERS  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept": "text/html"}
PATTERNS = {
    "workday": re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"),
    "greenhouse": re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)"
                             r"|boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),
    "smartrecruiters": re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    "eightfold": re.compile(r"([a-z0-9-]+\.eightfold\.ai)"),
    "oracle_hcm": re.compile(r"([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)[^\"']*?/sites/(CX_?\d+)"),
}
SKIP = {"embed", "js", "api", "v1", "jobs", "job_board", "search", "login", "introduceYourself", "en-US", "wday"}
FOLLOW = re.compile(r'href="([^"#]*(?:job|career|opening|position|join)[^"#]*)"', re.I)


def fetch(url: str) -> tuple[str, str]:
    if not url.startswith("http"):
        url = "https://" + url
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read(1_500_000).decode("utf-8", "ignore"), r.geturl()


def candidates(html: str, final_url: str) -> list[dict]:
    out = []
    for m in PATTERNS["workday"].finditer(html):
        tenant, wd, site = m.groups()
        if site not in SKIP:
            out.append({"type": "workday", "host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site})
    for m in PATTERNS["greenhouse"].finditer(html):
        slug = m.group(1) or m.group(2)
        if slug and slug.lower() not in SKIP:
            out.append({"type": "greenhouse", "slug": slug})
    for t in ("lever", "ashby", "smartrecruiters"):
        for slug in set(PATTERNS[t].findall(html)):
            if slug.lower() not in SKIP:
                out.append({"type": t, "slug": slug})
    for host in set(PATTERNS["eightfold"].findall(html)):
        if host not in ("app.eightfold.ai", "eightfold.ai"):
            out.append({"type": "eightfold", "host": host})
    for host, site in set(PATTERNS["oracle_hcm"].findall(html)):
        out.append({"type": "oracle_hcm", "host": host, "site": site})
    if re.search(r"jibeapply|jibecdn", html):
        out.append({"type": "jibe", "host": re.sub(r"^https?://([^/]+).*", r"\1", final_url)})
    if re.search(r"tbcdn\.talentbrew|talentbrew\.com", html):
        out.append({"type": "talentbrew", "host": re.sub(r"^https?://([^/]+).*", r"\1", final_url)})
    uniq, seen = [], set()
    for c in out:
        k = tuple(sorted(c.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def try_source(company: str, c: dict) -> int:
    cfg = {"company": company, **c}
    if c["type"] == "eightfold":
        domain = c["host"].split(".")[0] + ".com"
        cfg["domain"] = domain
    try:
        return len(ADAPTERS[c["type"]](cfg))
    except Exception:
        return 0


def detect(item: tuple[str, list[str]]):
    company, urls = item
    pages = []
    for u in urls:
        try:
            html, final = fetch(u)
            pages.append((html, final))
            # one level deeper: the "Jobs"/"Open positions" link usually reveals the ATS
            for link in list(dict.fromkeys(FOLLOW.findall(html)))[:6]:
                if link.startswith("/"):
                    link = re.sub(r"^(https?://[^/]+).*", r"\1", final) + link
                if link.startswith("http"):
                    try:
                        pages.append(fetch(link))
                    except Exception:
                        pass
        except Exception:
            continue
    cands = []
    for html, final in pages:
        cands += [c for c in candidates(html, final) if c not in cands]
    best = None
    for c in cands:
        n = try_source(company, c)
        if n and (best is None or n > best[1]):
            best = (c, n)
    return company, best, [c["type"] for c in cands]


def main(path: str) -> None:
    items = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "|" in line:
            name, urls = line.split("|", 1)
            items.append((name.strip(), [u.strip() for u in urls.split(",") if u.strip()]))
    found, missed = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for company, best, seen in ex.map(detect, items):
            if best:
                found.append((company, *best))
            else:
                missed.append(f"{company}{' (saw: ' + ','.join(sorted(set(seen))) + ')' if seen else ''}")
    for company, c, n in found:
        body = "\n".join(f'{k} = "{v}"' for k, v in c.items() if k != "type")
        print(f'[[source]]\ntype = "{c["type"]}"\ncompany = "{company}"\n{body}   # {n} jobs fetched\n')
    print(f"# found {len(found)}, missed {len(missed)}")
    print("# missed: " + "; ".join(missed))


if __name__ == "__main__":
    main(sys.argv[1])
