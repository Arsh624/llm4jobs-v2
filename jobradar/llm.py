"""gpt-4o-mini fallback for years-of-experience, used only when the regex finds nothing."""
from __future__ import annotations

import json
import logging
import os

from .net import request_json

LOG = logging.getLogger(__name__)

PROMPT = (
    "You read a job description and return the MINIMUM years of professional experience "
    "required. Use the lowest number if several paths are given (e.g. '5 yrs, or 3 with a PhD' -> 3). "
    "Internships and degrees do not count as years. If no requirement is stated, return null. "
    'Respond with JSON only: {"min_years": <integer or null>}'
)


def llm_min_years(description: str, model: str = "gpt-4o-mini") -> int | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not description.strip():
        return None
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 20,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": description[:6000]},
        ],
    }
    try:
        d = request_json("https://api.openai.com/v1/chat/completions", body,
                         headers={"Authorization": f"Bearer {key}"}, timeout=40, retries=2)
        v = json.loads(d["choices"][0]["message"]["content"]).get("min_years")
        return int(v) if v is not None else None
    except Exception as e:
        LOG.warning("llm_min_years failed: %s", e)
        return None
