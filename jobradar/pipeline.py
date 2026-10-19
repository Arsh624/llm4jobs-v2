"""fetch -> date/seen -> title filters -> details -> location -> years -> dedupe -> digest."""
from __future__ import annotations

import logging
import os
import tomllib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import filters
from .digest import send_email, write_outputs
from .llm import llm_min_years
from .models import Job
from .sources import ADAPTERS, fetch_detail
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
    "llm_max_calls": 60,
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
        jobs = fn(src, max_age_days=settings["max_age_days"]) if src["type"] == "workday" else fn(src)
        return src, jobs, None
    except Exception as e:
        return src, [], f"{type(e).__name__}: {str(e)[:150]}"


def run(config_path: Path, state_path: Path, out_dir: Path, *, send: bool) -> dict:
    settings, sources = load_config(config_path)
    state = State(state_path)
    stats = Counter()
    errors: dict[str, str] = {}

    # 1. fetch every source in parallel
    all_jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for src, jobs, err in ex.map(lambda s: _fetch_one(s, settings), sources):
            label = src.get("company", src["type"])
            if err:
                errors[label] = err
                LOG.warning("source failed: %s (%s) %s", label, src["type"], err)
            stats["fetched"] += len(jobs)
            all_jobs.extend(jobs)
    LOG.info("fetched %d jobs from %d sources (%d failed)", len(all_jobs), len(sources), len(errors))

    # 2. cheap filters: already seen, too old, title rules
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings["max_age_days"])
    cands: list[Job] = []
    for j in all_jobs:
        if j.key in state.seen:
            stats["already_seen"] += 1
            continue
        if j.posted_at is None:
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

    # 3. lazy detail fetch (Workday / SmartRecruiters descriptions), only for survivors
    def _detail(j: Job):
        try:
            fetch_detail(j)
        except Exception as e:
            LOG.debug("detail failed %s: %s", j.url, e)
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_detail, cands))

    # 4. location + years
    keep: list[Job] = []
    llm_calls = 0
    for j in cands:
        if settings["us_only"] and not (j.extra.get("wd_us_facet") or
                                        filters.is_us(j.location, settings["remote_counts_as_us"])):
            stats["drop_non_us"] += 1
            state.mark_seen(j.key)
            continue
        if j.min_years is None:
            j.min_years = filters.extract_min_years(j.description)
            j.years_basis = "regex" if j.min_years is not None else "not stated"
            if j.min_years is None and settings["llm_fallback"] and j.description:
                hit, cached = state.cached_years(j.key)
                if hit:
                    j.min_years = cached
                elif llm_calls < settings["llm_max_calls"]:
                    llm_calls += 1
                    j.min_years = llm_min_years(j.description, settings["llm_model"])
                    state.cache_years(j.key, j.min_years)
                if j.min_years is not None:
                    j.years_basis = "llm"
        if j.min_years is not None and j.min_years > settings["max_years"]:
            stats["drop_years"] += 1
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

    # 6. output -- state is saved only after a successful send, so a failed email retries next run
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
    state.save()

    stats.update({"new_jobs": len(final), "llm_calls": llm_calls})
    return {"stats": dict(stats), "errors": errors, "jobs": final, "bootstrap": state.is_bootstrap}


def email_enabled() -> bool:
    return os.environ.get("EMAIL_ENABLED", "false").strip().lower() in ("1", "true", "yes")
