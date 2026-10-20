"""fetch -> date/seen -> title filters -> details -> location -> years (regex) -> AI check
-> dedupe -> digest, plus a broken-feed alert."""
from __future__ import annotations

import json
import logging
import os
import tomllib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import filters
from .digest import send_alert, send_email, write_alert, write_outputs
from .llm import llm_assess, llm_available
from .models import Job
from .sources import ADAPTERS, AGE_AWARE, fetch_detail
from .store import State

LOG = logging.getLogger(__name__)

DEFAULTS = {
    "max_age_days": 3,
    "max_years": 2,
    "us_only": True,
    "remote_counts_as_us": True,
    "include_internships": False,
    "role_keywords": [],
    "role_exclude": [],
    "llm_fallback": True,
    "llm_model": "gpt-4o-mini",
    "llm_max_calls": 150,       # per run
    "llm_daily_cap": 600,       # per day (~$0.18/day worst case with gpt-4o-mini)
    "alert_after_runs": 4,      # consecutive failed runs before a feed is reported broken (~1h)
    "alert_min_typical_jobs": 20,  # a feed that usually has >= this many jobs and suddenly has 0 = broken
}
# When the same job shows up twice, keep the direct-from-company copy over the aggregator.
SOURCE_PRIORITY = {"simplify": 9}


def load_config(path: Path) -> tuple[dict, list[dict]]:
    cfg = tomllib.loads(path.read_text("utf-8"))
    settings = {**DEFAULTS, **cfg.get("settings", {})}
    sources = [s for s in cfg.get("source", []) if s.get("enabled", True)]
    return settings, sources


def _fetch_one(src: dict, settings: dict) -> tuple[dict, list[Job], str | None]:
    fn = ADAPTERS[src["type"]]
    try:
        jobs = fn(src, max_age_days=settings["max_age_days"]) if src["type"] in AGE_AWARE else fn(src)
        return src, jobs, None
    except Exception as e:
        return src, [], f"{type(e).__name__}: {str(e)[:150]}"


def run(config_path: Path, state_path: Path, out_dir: Path, *, send: bool) -> dict:
    settings, sources = load_config(config_path)
    state = State(state_path)
    stats = Counter()
    errors: dict[str, str] = {}
    max_years = settings["max_years"]

    # 1. fetch every source in parallel
    all_jobs: list[Job] = []
    health: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for src, jobs, err in ex.map(lambda s: _fetch_one(s, settings), sources):
            label = src.get("company", src["type"])
            if err:
                errors[label] = err
                LOG.warning("source failed: %s (%s) %s", label, src["type"], err)
            health[label] = {"type": src["type"], "jobs": len(jobs), "error": err}
            stats["fetched"] += len(jobs)
            all_jobs.extend(jobs)
    _write_health(state_path.parent / "health.json", health)
    broken, recovered = _update_feed_health(state, health, settings)
    LOG.info("fetched %d jobs from %d sources (%d failed)", len(all_jobs), len(sources), len(errors))

    # 2. cheap filters: already seen, too old, title rules
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings["max_age_days"])
    cands: list[Job] = []
    for j in all_jobs:
        if j.key in state.seen:
            stats["already_seen"] += 1
            continue
        if j.posted_at is None and j.extra.get("date_from_detail"):
            pass                              # dated after the detail fetch (step 4)
        elif j.posted_at is None:
            if state.is_bootstrap:            # first run: don't flood with undated jobs
                state.mark_seen(j.key)
                stats["undated_bootstrap"] += 1
                continue
        elif j.posted_at < cutoff:
            stats["too_old"] += 1
            continue
        reason = None
        if not settings["include_internships"] and filters.is_intern(j.title):
            reason = "intern"
        elif filters.is_senior(j.title):
            reason = "senior_title"
        elif (not filters.matches_role(j.title, settings["role_keywords"])
              or (settings["role_exclude"] and filters.matches_role(j.title, settings["role_exclude"]))):
            reason = "role"
        if reason:
            stats[f"drop_{reason}"] += 1
            state.mark_seen(j.key)
            continue
        cands.append(j)

    # 3. lazy detail fetch (descriptions / dates), only for survivors
    def _detail(j: Job):
        try:
            fetch_detail(j)
        except Exception as e:
            LOG.debug("detail failed %s: %s", j.url, e)
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_detail, cands))

    # 4a. date (for date-less lists), location, regex years -- all free
    pre: list[Job] = []
    for j in cands:
        if j.extra.get("date_from_detail"):
            if j.posted_at is None:           # detail fetch failed: retry next run, don't mark seen
                stats["detail_failed_retry"] += 1
                continue
            if j.posted_at < cutoff:          # first time we see an old job on a date-less list
                stats["too_old"] += 1
                state.mark_seen(j.key)
                continue
        # Always check location ourselves: some Workday tenants silently ignore the US facet.
        if settings["us_only"] and not filters.is_us(j.location, settings["remote_counts_as_us"]):
            stats["drop_non_us"] += 1
            state.mark_seen(j.key)
            continue
        if j.min_years is None:
            regex = filters.extract_min_years(j.description)
            if regex is not None:
                j.min_years, j.years_basis = regex, "regex"
        if j.min_years is not None and j.min_years > max_years:
            stats["drop_years"] += 1
            state.mark_seen(j.key)
            continue
        pre.append(j)

    # 4b. AI assessment of every survivor: parallel, cached, within the daily cap
    llm_calls = 0
    if settings["llm_fallback"]:
        need = []
        for j in pre:
            if not j.description:
                continue
            cached = state.cached_ai(j.key)
            if cached is not None:
                j.extra["ai"] = cached
            else:
                need.append(j)
        budget = 0
        if llm_available():
            budget = min(settings["llm_max_calls"], max(0, settings["llm_daily_cap"] - state.ai_calls_today()))
        to_call = need[:budget]
        if len(need) > len(to_call):
            stats["ai_skipped_cap"] = len(need) - len(to_call)
        with ThreadPoolExecutor(max_workers=6) as ex:
            for j, (ok, res) in zip(to_call, ex.map(
                    lambda j: llm_assess(j.title, j.company, j.description, settings["llm_model"]), to_call)):
                state.count_ai_call()
                llm_calls += 1
                if ok:
                    state.cache_ai(j.key, res)
                    j.extra["ai"] = res

    # 4c. decide. The AI only rejects on clear evidence; unclear postings are kept.
    keep: list[Job] = []
    for j in pre:
        ai = j.extra.get("ai")
        if ai and j.min_years is None and ai.get("min_years") is not None:
            j.min_years, j.years_basis = ai["min_years"], "ai"
        if j.min_years is None:
            j.years_basis = j.years_basis or "not stated"
        if j.min_years is not None and j.min_years > max_years:
            stats["drop_years_ai"] += 1
            state.mark_seen(j.key)
            continue
        if ai and (ai.get("level") == "senior" or (ai.get("level") == "mid" and j.min_years is None)):
            stats["drop_ai_level"] += 1
            state.mark_seen(j.key)
            continue
        keep.append(j)   # marked seen only once actually emailed (see step 6)

    # 5. dedupe across sources and against everything already emailed
    keep.sort(key=lambda j: (SOURCE_PRIORITY.get(j.source, 0),
                             -(j.posted_at.timestamp() if j.posted_at else 0)))
    final, batch_keys = [], set()
    for j in keep:
        if j.dedupe_key in state.sent or j.dedupe_key in batch_keys:
            stats["drop_duplicate"] += 1
            continue
        batch_keys.add(j.dedupe_key)
        final.append(j)
    final.sort(key=lambda j: -(j.posted_at.timestamp() if j.posted_at else 0))

    # 6. output -- state is saved only after a successful send, so a failed email retries next run.
    # In dry-run, qualifying jobs are NOT marked seen/sent, so they still arrive in the first
    # real email once EMAIL_ENABLED is turned on (as long as they're within max_age_days).
    write_outputs(final, out_dir)
    if final and send:
        send_email(final)
        stats["emailed"] = len(final)
        for j in keep:                   # includes cross-source duplicates of what we sent
            state.mark_seen(j.key)
        for j in final:
            state.mark_sent(j.dedupe_key)

    # 7. broken-feed alert (sent once per incident; "recovered" notes when they come back)
    if broken or recovered:
        write_alert(broken, recovered, out_dir)
        if send:
            try:
                send_alert(broken, recovered)
                for label, _ in broken:
                    state.feeds[label]["alerted"] = True
                stats["feed_alert_sent"] = len(broken) + len(recovered)
            except Exception as e:
                LOG.warning("feed alert email failed: %s", e)
    state.save()

    stats.update({"new_jobs": len(final), "llm_calls": llm_calls, "ai_calls_today": state.ai_calls_today()})
    return {"stats": dict(stats), "errors": errors, "jobs": final, "bootstrap": state.is_bootstrap}


def _update_feed_health(state: State, health: dict, settings: dict) -> tuple[list, list]:
    """Track failure streaks per source. Returns (newly broken [(label, reason)], recovered [label])."""
    broken, recovered = [], []
    for label, h in health.items():
        f = state.feeds.get(label, {"streak": 0, "alerted": False, "typical": 0})
        sudden_empty = h["jobs"] == 0 and f.get("typical", 0) >= settings["alert_min_typical_jobs"]
        if h["error"] or sudden_empty:
            f["streak"] = f.get("streak", 0) + 1
            f["reason"] = h["error"] or f"returned 0 jobs (usually ~{f['typical']})"
            if f["streak"] >= settings["alert_after_runs"] and not f.get("alerted"):
                broken.append((label, f["reason"]))
        else:
            if f.get("alerted"):
                recovered.append(label)
            f = {"streak": 0, "alerted": False, "typical": h["jobs"] or f.get("typical", 0)}
        state.feeds[label] = f
    return broken, recovered


def _write_health(path: Path, health: dict) -> None:
    """Per-source result of the latest run, so a broken feed is visible at a glance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "sources": dict(sorted(health.items()))}, indent=1), "utf-8")


def email_enabled() -> bool:
    return os.environ.get("EMAIL_ENABLED", "false").strip().lower() in ("1", "true", "yes")
