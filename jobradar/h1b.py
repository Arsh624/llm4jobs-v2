"""H-1B approval counts by employer (USCIS Employer Data Hub, FY2021-23), looked up by company name.

Matching is conservative: exact normalised name first; otherwise the largest employer whose legal name
STARTS WITH the company name (so "Amazon" -> "Amazon.com Services LLC", "Micron" -> "Micron Technology")
provided the company name is at least 6 characters. No record just means "no match under that name"."""
from __future__ import annotations

import re

SUFFIX = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co", "company",
          "pbc", "plc", "gmbh", "opco", "holdings", "holding", "the", "usa", "us", "america", "americas", "group"}


def norm(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", (name or "").lower()).replace("&", " and ")
    return " ".join(w for w in re.sub(r"[^a-z0-9]+", " ", n).split() if w not in SUFFIX)


def employer_keys(raw: str) -> set[str]:
    parts = re.split(r"\bd/?b/?a\b|\bf/?k/?a\b|\bformerly\b", raw, flags=re.I)    # "X DBA Y" -> both names
    return {k for k in (norm(p) for p in parts) if k}


ALIASES = {"meta": "meta platforms", "facebook": "meta platforms", "alphabet": "google", "aws": "amazon web services"}


def lookup(table: dict[str, int], company: str) -> int:
    variants = [norm(company), norm(re.sub(r"\s*[/(].*", "", company))]
    variants += [ALIASES[v] for v in variants if v in ALIASES]
    for k in variants:
        if k and table.get(k):
            return table[k]
    for k in variants:
        if len(k) >= 6:
            best = max((v for key, v in table.items() if key.startswith(k + " ")), default=0)
            if best:
                return best
    return 0
