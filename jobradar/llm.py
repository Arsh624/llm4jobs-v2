"""gpt-4o-mini job assessment: years of experience, real seniority level and a one-line note.

Runs on every job that survives the cheap filters (title, location, regex years). It only
rejects on clear evidence, so a vague description keeps the job rather than dropping it.
"""
from __future__ import annotations

import json
import logging
import os

from .net import request_json

LOG = logging.getLogger(__name__)

PROMPT = (
    "You screen job postings for a new graduate (0-2 years of experience) in the US.\n"
    "From the title and description, return JSON only:\n"
    '{"min_years": <integer or null>, "level": "entry" | "mid" | "senior" | "unknown", '
    '"note": "<max 12 words: why it does or doesn\'t fit a new grad>"}\n'
    "Rules:\n"
    "- min_years: the MINIMUM professional experience required. If several paths are given, use the "
    "lowest (\"5 yrs, or 3 with a PhD\" -> 3). Degrees and internships are not years. null if not stated.\n"
    "- level: entry = new grad / junior / 0-2 yrs; mid = clearly expects ~3-5 yrs; senior = 5+ yrs, "
    "leads teams, or principal/staff-level scope. Use unknown when the posting doesn't make it clear.\n"
    "- \"Member of Technical Staff\" is an all-levels title; judge it by the description."
)


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def llm_assess(title: str, company: str, description: str,
               model: str = "gpt-4o-mini") -> tuple[bool, dict | None]:
    """Returns (ok, {"min_years", "level", "note"}). ok=False means the call failed: don't cache."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not description.strip():
        return False, None
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 80,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": f"Title: {title}\nCompany: {company}\n\n{description[:6000]}"},
        ],
    }
    try:
        d = request_json("https://api.openai.com/v1/chat/completions", body,
                         headers={"Authorization": f"Bearer {key}"}, timeout=40, retries=2)
        out = json.loads(d["choices"][0]["message"]["content"])
        my = out.get("min_years")
        level = str(out.get("level") or "unknown").lower()
        return True, {
            "min_years": int(my) if my is not None else None,
            "level": level if level in ("entry", "mid", "senior") else "unknown",
            "note": str(out.get("note") or "")[:120],
        }
    except Exception as e:
        LOG.warning("llm_assess failed: %s", e)
        return False, None
