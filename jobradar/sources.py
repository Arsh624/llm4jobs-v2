"""Source adapters. Each takes one config entry and returns a list of Job.

All endpoints are public JSON used by the companies' own career sites; no keys needed.
Adapters return *listing-level* data; `fetch_detail` fills descriptions lazily, and only
for jobs that already passed the cheap title/location/date filters.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .models import Job, html_to_text
from .net import request_json

LOG = logging.getLogger(__name__)
UTC = timezone.utc
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _iso(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _epoch(v, ms=False) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(v) / (1000 if ms else 1), UTC)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Greenhouse
def greenhouse(cfg) -> list[Job]:
    slug = cfg["slug"]
    d = request_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        out.append(Job(
            source="greenhouse", source_id=f"{slug}:{j['id']}", company=cfg["company"],
            title=j.get("title", ""), url=j.get("absolute_url", ""),
            location=(j.get("location") or {}).get("name", ""),
            posted_at=_iso(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j.get("content", "")),
        ))
    return out


# --------------------------------------------------------------------- Ashby
def ashby(cfg) -> list[Job]:
    slug = cfg["slug"]
    d = request_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}")   # some slugs have spaces
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = "; ".join(x for x in locs if x)
        if country and country not in loc:
            loc = f"{loc} ({country})" if loc else country
        if j.get("isRemote") and "remote" not in loc.lower():
            loc += " (Remote)"
        out.append(Job(
            source="ashby", source_id=f"{slug}:{j['id']}", company=cfg["company"],
            title=j.get("title", ""), url=j.get("jobUrl", ""), location=loc,
            posted_at=_iso(j.get("publishedAt")),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
        ))
    return out


# --------------------------------------------------------------------- Lever
def lever(cfg) -> list[Job]:
    slug = cfg["slug"]
    api = "api.eu.lever.co" if cfg.get("region") == "eu" else "api.lever.co"   # EU-hosted boards
    d = request_json(f"https://{api}/v0/postings/{slug}?mode=json")
    out = []
    for j in d if isinstance(d, list) else []:
        cats = j.get("categories") or {}
        lists = "\n".join(f"{l.get('text','')}\n{html_to_text(l.get('content',''))}" for l in j.get("lists") or [])
        out.append(Job(
            source="lever", source_id=f"{slug}:{j['id']}", company=cfg["company"],
            title=j.get("text", ""), url=j.get("hostedUrl", ""),
            location="; ".join(cats.get("allLocations") or [cats.get("location") or ""]),
            posted_at=_epoch(j.get("createdAt"), ms=True),
            description=f"{j.get('descriptionPlain','')}\n{lists}".strip(),
        ))
    return out


# ------------------------------------------------------------------- Workday
WD_US = "bc33aa3152ec42d4995f4791a106ed09"   # Workday's global id for United States
_WD_AGE = re.compile(r"(\d+)\+?\s*Days?\s*Ago", re.I)


def _wd_age_days(posted_on: str) -> int:
    s = (posted_on or "").lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = _WD_AGE.search(posted_on or "")
    return int(m.group(1)) if m else 999


def workday(cfg, max_age_days: int = 3, max_pages: int = 15) -> list[Job]:
    host, tenant, site = cfg["host"], cfg["tenant"], cfg["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    facets = {"locationCountry": [WD_US]}
    out, offset = [], 0
    for _ in range(max_pages):
        body = {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": cfg.get("search", "")}
        try:
            d = request_json(f"{base}/jobs", body)
        except Exception as e:
            if facets and "400" in str(e):      # tenant doesn't support the US facet
                facets = {}
                continue
            raise
        posts = d.get("jobPostings") or []
        if not posts:
            break
        too_old = 0
        for p in posts:
            age = _wd_age_days(p.get("postedOn", ""))
            if age > max_age_days:
                too_old += 1
                continue
            path = p.get("externalPath", "")
            out.append(Job(
                source="workday", source_id=f"{tenant}:{path.rsplit('_', 1)[-1] or path}",
                company=cfg["company"], title=p.get("title", ""),
                url=f"https://{host}/en-US/{site}{path}", location=p.get("locationsText", ""),
                posted_at=datetime.now(UTC) - timedelta(days=age),
                extra={"wd_detail": f"{base}{path}", "wd_us_facet": bool(facets)},
            ))
        if too_old == len(posts):              # newest-first: a full page of old jobs means we're done
            break
        offset += 20
    return out


def _workday_detail(job: Job) -> None:
    info = request_json(job.extra["wd_detail"]).get("jobPostingInfo") or {}
    job.description = html_to_text(info.get("jobDescription", ""))
    if info.get("startDate"):
        job.posted_at = _iso(info["startDate"] + "T00:00:00+00:00") or job.posted_at
    country = (info.get("country") or {}).get("descriptor", "")
    if country:
        job.location = f"{job.location} ({country})"


# ------------------------------------------------------------ SmartRecruiters
def smartrecruiters(cfg, max_pages: int = 10) -> list[Job]:
    cid = cfg["slug"]
    out, offset = [], 0
    for _ in range(max_pages):
        d = request_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100&offset={offset}&country=us")
        content = d.get("content") or []
        for p in content:
            loc = p.get("location") or {}
            out.append(Job(
                source="smartrecruiters", source_id=f"{cid}:{p['id']}", company=cfg["company"],
                title=p.get("name", ""), url=f"https://jobs.smartrecruiters.com/{cid}/{p['id']}",
                location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                + (" (Remote)" if loc.get("remote") else ""),
                posted_at=_iso(p.get("releasedDate")),
                extra={"sr_detail": p.get("ref")},
            ))
        offset += 100
        if offset >= (d.get("totalFound") or 0):
            break
    return out


def _smartrecruiters_detail(job: Job) -> None:
    d = request_json(job.extra["sr_detail"])
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    job.description = "\n".join(html_to_text((s or {}).get("text", "")) for s in sections.values())


# -------------------------------------------------------------------- Amazon
def amazon(cfg, pages: int = 3) -> list[Job]:
    q = quote(cfg.get("search", "software engineer"))
    out = []
    for page in range(pages):
        d = request_json(
            "https://www.amazon.jobs/en/search.json?country=USA&sort=recent"
            f"&result_limit=100&offset={page * 100}&base_query={q}")
        jobs = d.get("jobs") or []
        for j in jobs:
            try:
                posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=UTC)
            except ValueError:
                posted = None
            out.append(Job(
                source="amazon", source_id=str(j.get("id_icims") or j.get("id")), company=cfg["company"],
                title=j.get("title", ""), url="https://www.amazon.jobs" + (j.get("job_path") or ""),
                location=j.get("normalized_location") or j.get("location", ""), posted_at=posted,
                description=html_to_text("\n".join(
                    j.get(k, "") for k in ("basic_qualifications", "preferred_qualifications", "description_short"))),
            ))
        if len(jobs) < 100:
            break
    return out


# ------------------------------------------------------------- iCIMS Jibe
def _jibe_dt(s):
    # "2026-10-19T23:03:00+0000" -> make the offset ISO-compatible on every Python version
    return _iso(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s or ""))


def jibe(cfg, max_age_days: int = 3, max_pages: int = 10) -> list[Job]:
    """iCIMS Jibe career sites (AMD, GitHub, ...): /api/jobs, newest first."""
    host = cfg["host"]
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    out = []
    for page in range(1, max_pages + 1):
        d = request_json(f"https://{host}/api/jobs?page={page}&limit=100&sortBy=posted_date"
                         "&descending=true&internal=false")
        jobs = [x.get("data") or {} for x in d.get("jobs") or []]
        if not jobs:
            break
        for j in jobs:
            posted = _jibe_dt(j.get("posted_date"))
            loc = j.get("full_location") or j.get("location_name") or ""
            if j.get("country_code") and j.get("country_code") not in loc:
                loc = f"{loc} ({j['country_code']})"
            out.append(Job(
                source="jibe", source_id=f"{host}:{j.get('req_id') or j.get('slug')}", company=cfg["company"],
                title=j.get("title", ""), location=loc, posted_at=posted,
                url=(j.get("meta_data") or {}).get("canonical_url") or f"https://{host}/jobs/{j.get('slug')}",
                description=html_to_text("\n".join(j.get(k) or "" for k in ("description", "qualifications", "responsibilities"))),
            ))
        last = _jibe_dt(jobs[-1].get("posted_date"))
        if last and last < cutoff:              # newest-first: everything after this is older
            break
    return out


# ---------------------------------------------------------- Eightfold PCSX
_EF_SESSIONS: dict[str, tuple] = {}


def _eightfold_session(host: str):
    """Eightfold's PCSX API needs the site's cookies + CSRF token, like their own page uses."""
    if host not in _EF_SESSIONS:
        import http.cookiejar
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        req = urllib.request.Request(f"https://{host}/careers", headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        page = opener.open(req, timeout=30).read().decode("utf-8", "ignore")
        m = re.search(r'name="_csrf" content="([^"]+)"', page)
        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                   "Referer": f"https://{host}/careers"}
        if m:
            headers["X-CSRFToken"] = m.group(1)
        _EF_SESSIONS[host] = (opener, headers)
    return _EF_SESSIONS[host]


def _eightfold_get(host: str, path: str) -> dict:
    import json as _json
    import urllib.request
    opener, headers = _eightfold_session(host)
    with opener.open(urllib.request.Request(f"https://{host}{path}", headers=headers), timeout=30) as r:
        return _json.load(r)


def eightfold(cfg, max_age_days: int = 3, max_pages: int = 40) -> list[Job]:
    """Eightfold PCSX career sites (Qualcomm, Microsoft, ...): 10 per page, newest first."""
    host, domain = cfg["host"], cfg["domain"]
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    out = []
    for page in range(max_pages):
        d = _eightfold_get(host, f"/api/pcsx/search?domain={domain}&query=&location=United%20States"
                                 f"&start={page * 10}&sort_by=timestamp")
        pos = (d.get("data") or {}).get("positions") or []
        if not pos:
            break
        for p in pos:
            out.append(Job(
                source="eightfold", source_id=f"{domain}:{p['id']}", company=cfg["company"],
                title=p.get("name", ""), url=f"https://{host}/careers/job/{p['id']}",
                location="; ".join(p.get("locations") or p.get("standardizedLocations") or []),
                posted_at=_epoch(p.get("postedTs") or p.get("creationTs")),
                extra={"ef": (host, domain, p["id"])},
            ))
        last = _epoch(pos[-1].get("postedTs") or pos[-1].get("creationTs"))
        if last and last < cutoff:
            break
    return out


def _eightfold_detail(job: Job) -> None:
    host, domain, pid = job.extra["ef"]
    det = _eightfold_get(host, f"/api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en").get("data") or {}
    job.description = html_to_text(det.get("jobDescription", ""))
    job.url = det.get("publicUrl") or job.url


# -------------------------------------------------- Radancy / TalentBrew
def _tb_items(results_html: str) -> list[tuple[str, str, str, str, str]]:
    """Parse TalentBrew result markup, which varies by company. Returns (path, id, title, location, date)."""
    items = []
    for chunk in re.split(r"(?=<a\s)", results_html):
        m = re.match(r'<a\s[^>]*href="(/(?:[^"]*?/)?job/[^"]+)"[^>]*>', chunk)   # "/job/..." or "/en/job/..."
        if not m:
            continue
        tag, path = m.group(0), m.group(1)
        jid = path.rstrip("/").rsplit("/", 1)[-1]
        t = re.search(r'data-title="([^"]+)"', tag) or re.search(r"<h2[^>]*>(.*?)</h2>", chunk, re.S)
        loc = re.search(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>', chunk, re.S)
        date = re.search(r'class="[^"]*job-date-posted[^"]*"[^>]*>(.*?)</span>', chunk, re.S)
        if t:
            items.append((path, jid, t.group(1), loc.group(1) if loc else "", date.group(1).strip() if date else ""))
    return items


def talentbrew(cfg, pages: int = 15) -> list[Job]:
    """Radancy/TalentBrew career sites (Synopsys, ...). The list has no dates and isn't strictly
    newest-first, so we read every page (cheap: 100 per page) and take the date, description and
    exact location from each new job's JSON-LD (fetched lazily, once per job)."""
    host = cfg["host"]
    out = []
    for page in range(1, pages + 1):
        d = request_json(
            f"https://{host}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage=100"
            "&Distance=50&RadiusUnitType=0&Keywords=&Location=&ShowRadius=False&IsPagination=False"
            "&CustomFacetName=&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results"
            "&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=1&SearchType=5",
            headers={"X-Requested-With": "XMLHttpRequest"})
        items = _tb_items(d.get("results") or "")
        if not items:
            break
        for path, jid, title, loc, date in items:
            posted = None
            if date:                                    # some sites (Palo Alto) list the date: "10/19/2026"
                try:
                    posted = datetime.strptime(date, "%m/%d/%Y").replace(tzinfo=UTC)
                except ValueError:
                    posted = None
            out.append(Job(
                source="talentbrew", source_id=f"{host}:{jid}", company=cfg["company"],
                title=html_to_text(title), url=f"https://{host}{path}", location=html_to_text(loc),
                posted_at=posted,
                extra={"tb_detail": f"https://{host}{path}", "date_from_detail": posted is None},
            ))
    return out


def _talentbrew_detail(job: Job) -> None:
    import json as _json
    import urllib.request
    req = urllib.request.Request(job.extra["tb_detail"], headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = _json.loads(blk)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            job.posted_at = _iso(f"{data.get('datePosted')}T00:00:00+00:00") if data.get("datePosted") else None
            job.description = html_to_text(data.get("description", ""))
            locs = data.get("jobLocation") or []
            countries = {((l.get("address") or {}).get("addressCountry") or "") for l in (locs if isinstance(locs, list) else [locs])}
            if countries - {""}:
                job.location = f"{job.location} ({', '.join(sorted(countries - {''}))})"
            return


# ---------------------------------------------------- Oracle Recruiting Cloud
def oracle_hcm(cfg, max_age_days: int = 3, max_pages: int = 10) -> list[Job]:
    """Oracle Recruiting Cloud (Oracle, Uber, ...): public recruitingCEJobRequisitions REST API,
    newest first."""
    host, site = cfg["host"], cfg["site"]
    job_base = cfg.get("job_url_base") or f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/"
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    out = []
    for page in range(max_pages):
        d = request_json(
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},"
            f"limit=50,offset={page * 50},sortBy=POSTING_DATES_DESC")
        reqs = ((d.get("items") or [{}])[0].get("requisitionList")) or []
        if not reqs:
            break
        for r in reqs:
            posted = _iso(f"{r['PostedDate']}T00:00:00+00:00") if r.get("PostedDate") else None
            locs = [r.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
            out.append(Job(
                source="oracle_hcm", source_id=f"{host}:{r['Id']}", company=cfg["company"],
                title=r.get("Title", ""), url=f"{job_base}{r['Id']}", location="; ".join(x for x in locs if x),
                posted_at=posted,
                description=html_to_text("\n".join(r.get(k) or "" for k in (
                    "ShortDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))),
                extra={"orc": (host, site, r["Id"])},
            ))
        last = _iso(f"{reqs[-1]['PostedDate']}T00:00:00+00:00") if reqs[-1].get("PostedDate") else None
        if last and last < cutoff:
            break
    return out


def _oracle_hcm_detail(job: Job) -> None:
    """The list only has short summaries; fetch the full description for the AI check."""
    host, site, rid = job.extra["orc"]
    d = request_json(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
        f'&onlyData=true&finder=ById;Id="{rid}",siteNumber={site}')
    item = (d.get("items") or [{}])[0]
    full = html_to_text("\n".join(item.get(k) or "" for k in (
        "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
    if len(full) > len(job.description):
        job.description = full


# ------------------------------------------------------------ SimplifyJobs
SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"


def simplify(cfg) -> list[Job]:
    """Community-curated new-grad list. Covers Google/Meta/Apple/Microsoft etc."""
    out = []
    for j in request_json(cfg.get("url", SIMPLIFY_URL), timeout=60):
        if not j.get("active") or j.get("is_visible") is False:
            continue
        out.append(Job(
            source="simplify", source_id=str(j.get("id")), company=j.get("company_name", ""),
            title=j.get("title", ""), url=j.get("url", ""),
            location="; ".join(j.get("locations") or []),
            posted_at=_epoch(j.get("date_posted")),
            min_years=0, years_basis="curated new-grad",
            sponsorship=j.get("sponsorship", ""),
            extra={"category": j.get("category", "")},
        ))
    return out


ADAPTERS = {
    "greenhouse": greenhouse, "ashby": ashby, "lever": lever, "workday": workday,
    "smartrecruiters": smartrecruiters, "amazon": amazon, "simplify": simplify,
    "jibe": jibe, "eightfold": eightfold, "talentbrew": talentbrew, "oracle_hcm": oracle_hcm,
}
# Adapters that stop paging once jobs get older than max_age_days.
AGE_AWARE = {"workday", "jibe", "eightfold", "oracle_hcm"}

DETAIL = {"workday": _workday_detail, "smartrecruiters": _smartrecruiters_detail,
          "eightfold": _eightfold_detail, "talentbrew": _talentbrew_detail,
          "oracle_hcm": _oracle_hcm_detail}
# Sources whose list already has a description, but only a short one worth upgrading.
ALWAYS_DETAIL = {"oracle_hcm"}


def fetch_detail(job: Job) -> None:
    fn = DETAIL.get(job.source)
    if fn and (not job.description or job.source in ALWAYS_DETAIL):
        fn(job)
