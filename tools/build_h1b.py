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
sys.path.insert(0, str(ROOT))
from jobradar.h1b import employer_keys, lookup, norm  # noqa: E402


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
    tracked = {norm(x["company"]) for x in sources}
    table = {k: v for k, v in totals.items() if v >= 10 or k in tracked}   # small file: 10+ approvals, or a tracked company
    matched = sum(1 for s in sources if lookup(table, s["company"]))
    (ROOT / "config" / "h1b.json").write_text(
        json.dumps({"years": "FY2021-FY2023", "employers": dict(sorted(table.items()))}, separators=(",", ":")), "utf-8")
    print(f"{len(table)} employers stored; {matched} of {len(sources)} tracked companies matched", file=sys.stderr)


if __name__ == "__main__":
    main()
