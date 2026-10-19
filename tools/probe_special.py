"""Probe the non-standard sources (Workday, Amazon, Eightfold, SmartRecruiters, SimplifyJobs)."""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 llm4jobs-v2", "Accept": "application/json"}


def get(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA)
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def check(name, fn):
    try:
        print(f"OK   {name:28} {fn()}")
    except Exception as e:
        print(f"FAIL {name:28} {type(e).__name__}: {str(e)[:120]}")


WD_BODY = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "software"}


def wd(host, tenant, site):
    d = get(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", WD_BODY)
    p = d.get("jobPostings", [])
    return f"total={d.get('total')} first={p[0]['title'] if p else None!r} posted={p[0].get('postedOn') if p else None!r}"


check("workday visa", lambda: wd("visa.wd5.myworkdayjobs.com", "visa", "Visa"))
check("workday kla", lambda: wd("kla.wd1.myworkdayjobs.com", "kla", "Search"))
check("workday salesforce", lambda: wd("salesforce.wd12.myworkdayjobs.com", "salesforce", "External_Career_Site"))
check("workday nvidia", lambda: wd("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"))


def amazon():
    d = get("https://www.amazon.jobs/en/search.json?base_query=software%20engineer&country=USA&result_limit=10&sort=recent")
    j = d.get("jobs", [])
    return f"hits={d.get('hits')} first={j[0]['title'] if j else None!r} posted={j[0].get('posted_date') if j else None!r}"


check("amazon", amazon)


def eightfold(host, domain):
    d = get(f"https://{host}/api/apply/v2/jobs?domain={domain}&query=software&location=United%20States&num=10&sort_by=timestamp")
    p = d.get("positions", [])
    return f"count={d.get('count')} first={p[0].get('name') if p else None!r}"


check("eightfold microsoft", lambda: eightfold("apply.careers.microsoft.com", "microsoft.com"))
check("eightfold nvidia", lambda: eightfold("nvidia.eightfold.ai", "nvidia.com"))


def smart(company):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=10")
    c = d.get("content", [])
    return f"total={d.get('totalFound')} first={c[0]['name'] if c else None!r}"


check("smartrecruiters servicenow", lambda: smart("ServiceNow"))


def simplify():
    for branch in ("dev", "main"):
        try:
            d = get(f"https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/{branch}/.github/scripts/listings.json")
            active = [x for x in d if x.get("active") and x.get("is_visible", True)]
            return f"branch={branch} total={len(d)} active={len(active)} keys={sorted(d[0].keys())}"
        except Exception:
            continue
    raise RuntimeError("no branch worked")


check("simplify new-grad", simplify)
