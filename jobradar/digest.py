"""Build the HTML/CSV digest and send it (or write it to disk in dry-run mode)."""
from __future__ import annotations

import csv
import html
import io
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .models import Job


def _ago(dt: datetime | None) -> str:
    if not dt:
        return "?"
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return f"{max(h, 0):.0f}h ago" if h < 48 else f"{h / 24:.0f}d ago"


def _years(j: Job) -> str:
    return "not stated" if j.min_years is None else str(j.min_years)


def render_html(jobs: list[Job]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(j.company)}</td>"
        f"<td><a href=\"{html.escape(j.url)}\">{html.escape(j.title)}</a></td>"
        f"<td>{html.escape(j.location[:60])}</td>"
        f"<td>{_years(j)}</td>"
        f"<td>{_ago(j.posted_at)}</td>"
        f"<td>{html.escape(j.sponsorship or '')}</td>"
        f"<td style=\"color:#888\">{j.source}</td>"
        "</tr>" for j in jobs)
    return (
        "<html><head><meta charset=\"utf-8\"></head><body style=\"font-family:Arial,sans-serif\">"
        f"<h2>Job Radar v2: {len(jobs)} new</h2>"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" style=\"border-collapse:collapse;font-size:13px\">"
        "<tr style=\"background:#f2f2f2\"><th>Company</th><th>Title</th><th>Location</th>"
        "<th>Min yrs</th><th>Posted</th><th>Sponsorship</th><th>Source</th></tr>"
        f"{rows}</table></body></html>")


def render_csv(jobs: list[Job]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "title", "location", "min_years", "posted_at", "sponsorship", "source", "url"])
    for j in jobs:
        w.writerow([j.company, j.title, j.location, _years(j),
                    j.posted_at.isoformat() if j.posted_at else "", j.sponsorship, j.source, j.url])
    return buf.getvalue()


def write_outputs(jobs: list[Job], out_dir: Path) -> None:
    """Always write the digest locally; jobs.json is the feed for the future dashboard."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "digest-latest.html").write_text(render_html(jobs), "utf-8")
    (out_dir / "digest-latest.csv").write_text(render_csv(jobs), "utf-8")
    feed_path = out_dir / "jobs.json"
    feed = json.loads(feed_path.read_text("utf-8")) if feed_path.exists() else []
    now = datetime.now(timezone.utc).isoformat()
    feed = [{
        "company": j.company, "title": j.title, "url": j.url, "location": j.location,
        "min_years": j.min_years, "years_basis": j.years_basis, "sponsorship": j.sponsorship,
        "source": j.source, "posted_at": j.posted_at.isoformat() if j.posted_at else None,
        "found_at": now, "description": j.description[:8000],
    } for j in jobs] + feed
    seen_urls, unique = set(), []
    for item in feed:                     # newest first; drop repeats from earlier runs
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique.append(item)
    feed_path.write_text(json.dumps(unique[:1500], indent=1), "utf-8")


def send_email(jobs: list[Job]) -> None:
    env = os.environ
    msg = EmailMessage()
    msg["Subject"] = f"Job Radar v2: {len(jobs)} new"
    msg["From"] = env["EMAIL_FROM"]
    msg["To"] = env["EMAIL_TO"]
    msg.set_content(render_csv(jobs))
    msg.add_alternative(render_html(jobs), subtype="html")
    msg.add_attachment(render_csv(jobs), subtype="csv", filename="new_jobs.csv")
    with smtplib.SMTP_SSL(env.get("SMTP_HOST", "smtp.gmail.com"), int(env.get("SMTP_PORT", "465"))) as s:
        s.login(env["SMTP_USER"], env["SMTP_PASS"].replace(" ", ""))
        s.send_message(msg)
