"""Probe which public ATS API (Greenhouse / Ashby / Lever) serves each company.

Usage: python tools/probe_ats.py
Prints a line per hit: <platform> <slug> <job_count>
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CANDIDATES = {
    "Pinterest": ["pinterest"],
    "OpenAI": ["openai"],
    "Scale AI": ["scaleai"],
    "xAI": ["xai"],
    "Cloudflare": ["cloudflare"],
    "Together AI": ["togetherai"],
    "ClickHouse": ["clickhouse"],
    "CoreWeave": ["coreweave"],
    "Fivetran": ["fivetran"],
    "Arize AI": ["arizeai", "arize"],
    "Databricks": ["databricks"],
    "DoorDash": ["doordash", "doordashusa"],
    "Airbnb": ["airbnb"],
    "Elastic": ["elastic"],
    "Pinecone": ["pinecone"],
    "Weaviate": ["weaviate"],
    "Braintrust": ["braintrust", "braintrustdata"],
    "Baseten": ["baseten"],
    "Atlan": ["atlan"],
    "LangChain": ["langchain"],
    "Modal": ["modal"],
    "Anyscale": ["anyscale"],
    "LanceDB": ["lancedb"],
    "Snowflake": ["snowflake"],
    "Meta": ["meta"],
    "Lenovo": ["lenovo"],
    "Bayview": ["bayview", "bayviewassetmanagement"],
    "Goldman Sachs": ["goldmansachs"],
    "ServiceNow": ["servicenow"],
    "Salesforce": ["salesforce"],
    "Stripe": ["stripe"],
    "Figma": ["figma"],
    "Notion": ["notion"],
    "Ramp": ["ramp"],
    "Plaid": ["plaid"],
    "Robinhood": ["robinhood"],
    "Discord": ["discord"],
    "Reddit": ["reddit"],
    "Roblox": ["roblox"],
    "Instacart": ["instacart"],
    "Lyft": ["lyft"],
    "Brex": ["brex"],
    "Anthropic": ["anthropic"],
    "Perplexity": ["perplexity", "perplexityai"],
    "Cohere": ["cohere"],
    "Mistral": ["mistral"],
    "Palantir": ["palantir"],
    "Rippling": ["rippling"],
    "Vercel": ["vercel"],
    "Supabase": ["supabase"],
}

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
}


def count(platform, slug):
    url = URLS[platform].format(s=slug)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "llm4jobs-v2-probe"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except Exception:
        return None
    if platform == "lever":
        return len(data) if isinstance(data, list) else None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return len(jobs) if isinstance(jobs, list) else None


def probe(item):
    company, slugs = item
    hits = []
    for slug in slugs:
        for platform in URLS:
            n = count(platform, slug)
            if n:
                hits.append(f"{platform}:{slug}({n})")
    return company, hits


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=12) as ex:
        for company, hits in ex.map(probe, CANDIDATES.items()):
            print(f"{company:15} {' '.join(hits) if hits else '-- none --'}")
    sys.stdout.flush()
