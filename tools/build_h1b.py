"""Build config/h1b.json: how many H-1B petitions each tracked company had approved (FY2021-2023).

Source: USCIS H-1B Employer Data Hub (public CSVs). Names are matched CONSERVATIVELY: the company's
name must equal the employer's legal name after removing "Inc/LLC/Corp/..." and punctuation. A miss
means "no record under that name" (new company, different legal name, or never filed), NOT "won't sponsor".

Usage: python tools/build_h1b.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIX = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co", "company",
          "pbc", "plc", "gmbh", "opco", "holdings", "holding", "the", "usa", "us", "america", "americas", "group"}


def norm(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", name.lower())
    n = n.replace("&", " and ")
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", n).split() if w not in SUFFIX]
    return " ".join(words)


def employer_keys(raw: str) -> set[str]:
    parts = re.split(r"\bd/?b/?a\b|\bf/?k/?a\b|\bformerly\b", raw, flags=re.I)   # "X DBA Y" -> both names
    return {k for k in (norm(p) for p in parts) if k}


def read_files(folder: Path) -> list[tuple[str, str]]:
    """USCIS blocks scripted downloads, so the CSVs are downloaded by hand into data/h1b/
    (h1b_datahubexport-2021.csv, -2022.csv, -2023.csv) from
    https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/h-1b-employer-data-hub-files"""
    files = sorted(folder.glob("h1b_datahubexport-*.csv"))
    if not files:
        sys.exit(f"No CSV files in {folder}. Download the FY2021-2023 files in your browser first (see docstring).")
    return [(f.stem.rsplit("-", 1)[-1], f.read_text("utf-8", "ignore")) for f in files]


def main() -> None:
    totals: dict[str, int] = defaultdict(int)
    for y, text in read_files(ROOT / "data" / "h1b"):
        rows = csv.DictReader(io.StringIO(text))
        for r in rows:
            emp = (r.get("Employer") or "").strip()
            if not emp:
                continue
            n = sum(int((r.get(c) or "0").replace(",", "") or 0) for c in ("Initial Approval", "Continuing Approval"))
            for k in employer_keys(emp):
                totals[k] += n
        print(f"FY{y} loaded", file=sys.stderr)

    sources = tomllib.load(open(ROOT / "config" / "sources.toml", "rb"))["source"]
    out = {}
    for s in sources:
        company = s["company"]
        for variant in {company, re.sub(r"\(.*?\)", "", company).strip(), re.sub(r"\s*/\s*.*", "", company)}:
            k = norm(variant)
            if k and totals.get(k, 0) > 0:
                out[company] = totals[k]
                break
    (ROOT / "config" / "h1b.json").write_text(
        json.dumps({"years": "FY2021-FY2023", "approvals": dict(sorted(out.items()))}, indent=1), "utf-8")
    print(f"matched {len(out)} of {len(sources)} companies", file=sys.stderr)


if __name__ == "__main__":
    main()
