"""gpt-4o-mini checks. Every call returns its real cost (from the API's token usage) so the
pipeline can enforce a hard daily dollar cap.

- llm_assess: full check of a job that passed the free filters -- years, level, US
  eligibility, fit for the user's profile, one-line note.
- llm_rescue: cheap second look at a title the seniority rule rejected on an ambiguous word
  ("Lead", "Staff", "Manager", ...), so mislabeled entry-level roles aren't lost.
"""
from __future__ import annotations

import json
import logging
import os

from .net import request_json

LOG = logging.getLogger(__name__)

# USD per 1M tokens (gpt-4o-mini list price). Override in config if OpenAI changes it.
PRICE = {"input": 0.15, "output": 0.60}

ASSESS_PROMPT = (
    "You screen job postings for a new graduate (0-2 years of experience) looking for work in the US.\n"
    "Return JSON only:\n"
    '{"min_years": <integer or null>, "level": "entry" | "mid" | "senior" | "unknown", '
    '"us_ok": true | false | null, "fit": <integer 0-10 or null>, '
    '"note": "<max 12 words: why it does or doesn\'t fit>"}\n'
    "Rules:\n"
    "- min_years: MINIMUM professional experience required; with several paths use the lowest "
    "(\"5 yrs, or 3 with a PhD\" -> 3). Degrees and internships are not years. null if not stated.\n"
    "- level: entry = new grad / junior / 0-2 yrs; mid = clearly expects ~3-5 yrs; senior = 5+ yrs, "
    "leads teams, or staff/principal scope. unknown if the posting doesn't make it clear.\n"
    "- \"Member of Technical Staff\" is an all-levels title; judge it by the description.\n"
    "- us_ok: false ONLY if the role clearly must be performed outside the US (e.g. 'must be based "
    "in Canada', EU-only remote). true if US-based or US-remote. null if unclear.\n"
    "- fit: how well the candidate profile matches this role (10 = excellent). null if no profile given."
)

RESCUE_PROMPT = (
    "A job title was flagged as senior by a keyword rule. Decide if the role is actually open to "
    "someone with 0-2 years of experience. Words like Lead, Staff, Manager, Head or Architect can "
    "be misleading (e.g. 'Lead Generation Analyst', 'Solutions Architect, Early Career', "
    "'Associate Product Manager'). Return JSON only: "
    '{"senior": true | false, "note": "<max 10 words>"}. If unsure, answer senior=true.'
)


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _call(system: str, user: str, model: str, max_tokens: int) -> tuple[bool, dict | None, float]:
    """Returns (ok, parsed_json, cost_usd). ok=False means failed: don't cache."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return False, None, 0.0
    body = {
        "model": model, "temperature": 0, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        d = request_json("https://api.openai.com/v1/chat/completions", body,
                         headers={"Authorization": f"Bearer {key}"}, timeout=40, retries=2)
        u = d.get("usage") or {}
        cost = (u.get("prompt_tokens", 0) * PRICE["input"] + u.get("completion_tokens", 0) * PRICE["output"]) / 1e6
        return True, json.loads(d["choices"][0]["message"]["content"]), cost
    except Exception as e:
        LOG.warning("llm call failed: %s", e)
        return False, None, 0.0


def llm_assess(title: str, company: str, location: str, description: str, profile: str = "",
               model: str = "gpt-4o-mini") -> tuple[bool, dict | None, float]:
    if not description.strip():
        return False, None, 0.0
    user = (f"Candidate profile:\n{profile.strip() or '(none given)'}\n\n"
            f"Title: {title}\nCompany: {company}\nListed location: {location}\n\n{description[:6000]}")
    ok, out, cost = _call(ASSESS_PROMPT, user, model, 100)
    if not ok:
        return False, None, cost
    my, fit = out.get("min_years"), out.get("fit")
    level = str(out.get("level") or "unknown").lower()
    us_ok = out.get("us_ok")
    return True, {
        "min_years": int(my) if isinstance(my, (int, float)) else None,
        "level": level if level in ("entry", "mid", "senior") else "unknown",
        "us_ok": us_ok if isinstance(us_ok, bool) else None,
        "fit": max(0, min(10, int(fit))) if isinstance(fit, (int, float)) and profile.strip() else None,
        "note": str(out.get("note") or "")[:120],
    }, cost


def llm_rescue(title: str, company: str, snippet: str,
               model: str = "gpt-4o-mini") -> tuple[bool, dict | None, float]:
    user = f"Title: {title}\nCompany: {company}\n\nDescription excerpt:\n{snippet[:1500]}"
    ok, out, cost = _call(RESCUE_PROMPT, user, model, 40)
    if not ok:
        return False, None, cost
    return True, {"senior": bool(out.get("senior", True)), "note": str(out.get("note") or "")[:100]}, cost
