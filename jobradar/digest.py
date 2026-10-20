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


def _fit(j: Job) -> str:
    f = (j.extra.get("ai") or {}).get("fit")
    return "" if f is None else f"{f}/10"


def _ai_note(j: Job) -> str:
    ai = j.extra.get("ai") or {}
    if not ai:
        return ""
    level = ai.get("level", "unknown")
    return f"{level}: {ai.get('note', '')}" if level != "unknown" else ai.get("note", "")


def render_html(jobs: list[Job]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(j.company)}</td>"
        f"<td><a href=\"{html.escape(j.url)}\">{html.escape(j.title)}</a></td>"
        f"<td>{html.escape(j.location[:60])}</td>"
        f"<td><b>{_fit(j)}</b></td>"
        f"<td>{_years(j)}</td>"
        f"<td>{_ago(j.posted_at)}</td>"
        f"<td style=\"font-size:12px\">{html.escape(_ai_note(j))}</td>"
        f"<td>{html.escape(j.sponsorship or '')}</td>"
        f"<td style=\"color:#888\">{j.source}</td>"
        "</tr>" for j in jobs)
    return (
        "<html><head><meta charset=\"utf-8\"></head><body style=\"font-family:Arial,sans-serif\">"
        f"<h2>Job Radar v2: {len(jobs)} new</h2>"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" style=\"border-collapse:collapse;font-size:13px\">"
        "<tr style=\"background:#f2f2f2\"><th>Company</th><th>Title</th><th>Location</th>"
        "<th>Fit</th><th>Min yrs</th><th>Posted</th><th>AI check</th><th>Sponsorship</th><th>Source</th></tr>"
        f"{rows}</table></body></html>")


def render_csv(jobs: list[Job]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "title", "location", "fit", "min_years", "posted_at", "ai_check", "sponsorship", "source", "url"])
    for j in jobs:
        w.writerow([j.company, j.title, j.location, _fit(j), _years(j),
                    j.posted_at.isoformat() if j.posted_at else "", _ai_note(j), j.sponsorship, j.source, j.url])
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
        "ai_check": _ai_note(j), "fit": (j.extra.get("ai") or {}).get("fit"),
        "found_at": now, "description": j.description[:8000],
    } for j in jobs] + feed
    seen_urls, unique = set(), []
    for item in feed:                     # newest first; drop repeats from earlier runs
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique.append(item)
    feed_path.write_text(json.dumps(unique[:1500], indent=1), "utf-8")


def _smtp_send(msg: EmailMessage) -> None:
    env = os.environ
    msg["From"] = env["EMAIL_FROM"]
    msg["To"] = env["EMAIL_TO"]
    with smtplib.SMTP_SSL(env.get("SMTP_HOST", "smtp.gmail.com"), int(env.get("SMTP_PORT", "465"))) as s:
        s.login(env["SMTP_USER"], env["SMTP_PASS"].replace(" ", ""))
        s.send_message(msg)


def send_email(jobs: list[Job]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Job Radar v2: {len(jobs)} new"
    msg.set_content(render_csv(jobs))
    msg.add_alternative(render_html(jobs), subtype="html")
    msg.add_attachment(render_csv(jobs), subtype="csv", filename="new_jobs.csv")
    _smtp_send(msg)


def _alert_text(broken: list, recovered: list) -> str:
    lines = []
    if broken:
        lines.append("These job feeds have been failing for about an hour, so their jobs are NOT being checked:\n")
        lines += [f"  - {label}: {reason}" for label, reason in broken]
        lines.append("\nDetails: state/health.json in the repo. Usually the company changed its careers site.")
    if recovered:
        lines.append("\nRecovered (working again): " + ", ".join(recovered))
    return "\n".join(lines)


def write_alert(broken: list, recovered: list, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "alert-latest.txt").write_text(_alert_text(broken, recovered), "utf-8")


def send_alert(broken: list, recovered: list) -> None:
    msg = EmailMessage()
    parts = []
    if broken:
        parts.append(f"{len(broken)} feed{'s' if len(broken) != 1 else ''} broken")
    if recovered:
        parts.append(f"{len(recovered)} recovered")
    msg["Subject"] = f"Job Radar v2 ALERT: {', '.join(parts)}"
    msg.set_content(_alert_text(broken, recovered))
    _smtp_send(msg)
