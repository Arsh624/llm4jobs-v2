"""python -m jobradar [--dry-run] [--config config/sources.toml]"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .pipeline import email_enabled, run

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. The .env file WINS over the shell environment on purpose: this
    machine has a stale OPENAI_API_KEY in the Windows user env that must never be used.
    (On GitHub Actions there is no .env, so repository secrets apply.)"""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def main() -> None:
    ap = argparse.ArgumentParser(prog="jobradar")
    ap.add_argument("--config", default=str(ROOT / "config" / "sources.toml"))
    ap.add_argument("--state", default=str(ROOT / "state" / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--dry-run", action="store_true", help="never send email, even if EMAIL_ENABLED=true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_dotenv(ROOT / ".env")
    send = email_enabled() and not args.dry_run

    res = run(Path(args.config), Path(args.state), Path(args.out), send=send)
    s = res["stats"]
    print(f"\n=== run summary ({'BOOTSTRAP ' if res['bootstrap'] else ''}{'email sent' if s.get('emailed') else 'no email'}) ===")
    for k in sorted(s):
        print(f"  {k:20} {s[k]}")
    if res["errors"]:
        print("  failed sources:")
        for k, v in res["errors"].items():
            print(f"    {k}: {v}")
    print(f"  digest written to {args.out}/digest-latest.html")


if __name__ == "__main__":
    main()
