"""fetch -> date/seen -> title filters -> details -> location -> years (regex) -> AI check
-> dedupe -> digest, plus a broken-feed alert."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tomllib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import filters
from .digest import send_alert, send_email, write_alert, write_outputs
from .llm import llm_assess, llm_available, llm_rescue
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
    "llm_max_calls": 150,       # full AI checks per run
    "llm_rescue_max": 60,       # AI second looks at ambiguous "senior" titles per run
    "llm_daily_usd_cap": 0.20,  # hard daily spend limit, measured from real token usage (~$6/month max)
    "home_area": "the San Francisco Bay Area",   # small hire-chance bonus for nearby/remote roles
    "alert_after_runs": 4,      # consecutive failed runs before a feed is reported broken (~1h)
    "alert_min_typical_jobs": 20,  # a feed that usually has >= this many jobs and suddenly has 0 = broken
}
# When the same job shows up twice, keep the direct-from-company copy over the aggregator.
SOURCE_PRIORITY = {"simplify": 9}
NEW_GRAD_TITLE = re.compile(r"new\s*grad|new\s*college|early\s*career|university|entry[\s-]*level|"
                            r"graduate|junior|associate|\bI\b|\b1\b", re.I)


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


EST_COST = {"assess": 0.0004, "rescue": 0.00012}   # USD, conservative upper estimates per call


def run(config_path: Path, state_path: Path, out_dir: Path, *, send: bool) -> dict:
    settings, sources = load_config(config_path)
    state = State(state_path)
    stats = Counter()
    errors: dict[str, str] = {}
    max_years = settings["max_years"]
    # Cache key carries the scoring version + home area, so changing either re-scores jobs once.
    ptag = "|v3|" + hashlib.sha1(settings["home_area"].encode()).hexdigest()[:6]

    def ai_budget(kind: str, wanted: int, run_cap: int) -> int:
        """How many AI calls of this kind we may still make (daily dollar cap + per-run cap)."""
        if not (settings["llm_fallback"] and llm_available()):
            return 0
        left_usd = max(0.0, settings["llm_daily_usd_cap"] - state.ai_usd_today())
        return min(wanted, run_cap, int(left_usd / EST_COST[kind]))

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
    rescue: list[Job] = []       # titles rejected only on an ambiguous word -> AI second look
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
        strength = filters.senior_strength(j.title)
        if not settings["include_internships"] and filters.is_intern(j.title):
            reason = "intern"
        elif (not filters.matches_role(j.title, settings["role_keywords"])
              or (settings["role_exclude"] and filters.matches_role(j.title, settings["role_exclude"]))):
            reason = "role"
        elif strength == "strong":
            reason = "senior_title"
        elif strength == "weak":
            rescue.append(j)
            continue
        if reason:
            stats[f"drop_{reason}"] += 1
            state.mark_seen(j.key)
            continue
        cands.append(j)

    # 3. lazy detail fetch (descriptions / dates), only for survivors and rescue candidates
    def _detail(j: Job):
        try:
            fetch_detail(j)
        except Exception as e:
            LOG.debug("detail failed %s: %s", j.url, e)
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_detail, cands + rescue))

    # 3b. AI second look at ambiguous "senior" titles (Lead / Staff / Manager / Architect ...)
    llm_calls = 0
    to_rescue = []
    for j in rescue:
        cached = state.cached_ai("rescue:" + j.key)
        if cached is not None:
            j.extra["rescue"] = cached
        else:
            to_rescue.append(j)
    allowed = ai_budget("rescue", len(to_rescue), settings["llm_rescue_max"])
    with ThreadPoolExecutor(max_workers=6) as ex:
        calls = to_rescue[:allowed]
        for j, (ok, res, cost) in zip(calls, ex.map(
                lambda j: llm_rescue(j.title, j.company, j.description, settings["llm_model"]), calls)):
            state.count_ai_call(cost)
            llm_calls += 1
            if ok:
                state.cache_ai("rescue:" + j.key, res)
                j.extra["rescue"] = res
    for j in rescue:
        r = j.extra.get("rescue")
        if r is None:                       # over budget / failed: try again next run, don't mark seen
            stats["rescue_deferred"] += 1
        elif r["senior"]:
            stats["drop_senior_title"] += 1
            state.mark_seen(j.key)
        else:
            stats["rescued_by_ai"] += 1
            cands.append(j)

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
        if settings["us_only"]:
            if not filters.is_us(j.location, settings["remote_counts_as_us"]):
                stats["drop_non_us"] += 1
                state.mark_seen(j.key)
                continue
            # "2 Locations", empty or bare "Remote": kept, and the AI check confirms from the text.
            j.extra["loc_unclear"] = filters.us_status(j.location, remote_counts=False) is None
        if j.min_years is None:
            regex = filters.extract_min_years(j.description)
            if regex is not None:
                j.min_years, j.years_basis = regex, "regex"
        if j.min_years is not None and j.min_years > max_years:
            stats["drop_years"] += 1
            state.mark_seen(j.key)
            continue
        pre.append(j)

    # 4b. AI check of every survivor (years, level, US eligibility, hire chance): parallel, cached,
    # within the daily dollar cap.
    need = []
    for j in pre:
        if not j.description:
            continue
        cached = state.cached_ai(j.key + ptag)
        if cached is not None:
            j.extra["ai"] = cached
        else:
            need.append(j)
    to_call = need[:ai_budget("assess", len(need), settings["llm_max_calls"])]
    if len(need) > len(to_call):
        stats["ai_skipped_cap"] = len(need) - len(to_call)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for j, (ok, res, cost) in zip(to_call, ex.map(
                lambda j: llm_assess(j.title, j.company, j.location, j.description, settings["home_area"],
                                     settings["llm_model"]), to_call)):
            state.count_ai_call(cost)
            llm_calls += 1
            if ok:
                state.cache_ai(j.key + ptag, res)
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
        if settings["us_only"] and ai and ai.get("us_ok") is False:
            stats["drop_non_us_ai"] += 1
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
    # Jobs without a description (SimplifyJobs) get no AI score; estimate one so curated new-grad
    # roles don't sink to the bottom. Shown with "~" in the email.
    for j in final:
        if (j.extra.get("ai") or {}).get("chance") is None:
            est = 8 if NEW_GRAD_TITLE.search(j.title) else (6 if j.source == "simplify" else 5)
            j.extra.setdefault("ai", {})
            j.extra["ai"] = {**j.extra["ai"], "chance": est, "chance_estimated": True}
    # Best hire chance first, then newest. Ordering only: nothing is dropped by this score.
    final.sort(key=lambda j: (-((j.extra.get("ai") or {}).get("chance") or -1),
                              -(j.posted_at.timestamp() if j.posted_at else 0)))

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

    stats.update({"new_jobs": len(final), "llm_calls": llm_calls, "ai_calls_today": state.ai_calls_today(),
                  "ai_usd_today": round(state.ai_usd_today(), 4)})
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
