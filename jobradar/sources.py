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
    d = request_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
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
    d = request_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
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
}

DETAIL = {"workday": _workday_detail, "smartrecruiters": _smartrecruiters_detail}


def fetch_detail(job: Job) -> None:
    fn = DETAIL.get(job.source)
    if fn and not job.description:
        fn(job)
