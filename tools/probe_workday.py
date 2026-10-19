"""Find a company's Workday board (host / tenant / site) by trying common combinations.

Usage: python tools/probe_workday.py redhat medtronic analogdevices ...
Prints a ready-to-paste [[source]] block for each hit.
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOSTS = ["wd1", "wd5", "wd3", "wd12", "wd103", "wd501"]
SITES = ["External", "ExternalCareerSite", "Careers", "jobs", "External_Career_Site", "Search",
         "{T}Careers", "{T}_Careers", "{T}", "{T}External", "careers"]
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Accept": "application/json"}
BODY = json.dumps({"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).encode()


def try_one(args):
    tenant, wd, site = args
    host = f"{tenant}.{wd}.myworkdayjobs.com"
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    try:
        req = urllib.request.Request(url, data=BODY, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            total = json.load(r).get("total", 0)
            return (tenant, host, site, total) if total else None
    except Exception:
        return None


def probe(tenant):
    combos = []
    for wd in HOSTS:
        for s in SITES:
            for variant in {s.replace("{T}", tenant), s.replace("{T}", tenant.capitalize())}:
                combos.append((tenant, wd, variant))
    with ThreadPoolExecutor(max_workers=12) as ex:
        hits = [h for h in ex.map(try_one, combos) if h]
    return max(hits, key=lambda h: h[3]) if hits else None


if __name__ == "__main__":
    for tenant in sys.argv[1:]:
        hit = probe(tenant)
        if hit:
            t, host, site, total = hit
            print(f'[[source]]\ntype = "workday"\ncompany = "{t}"\nhost = "{host}"\ntenant = "{t}"\nsite = "{site}"   # {total} open jobs\n')
        else:
            print(f"# {tenant}: no Workday board found")
