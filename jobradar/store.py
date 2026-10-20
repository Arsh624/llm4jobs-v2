"""State kept as one small JSON file so it can be committed back by GitHub Actions.

seen:  job keys already finished with (rejected, or emailed) -> date first seen
sent:  dedupe key (company|title) of every job we emailed -> date, to skip cross-source duplicates
years: cached LLM answers, key -> [min_years or null, date], so re-checks never cost twice
ai:    cached AI assessments, key -> [{"min_years", "level", "note"}, date]
usage: AI calls made today, {"date": ..., "calls": n} -- enforces the daily cost cap
feeds: per-source failure streaks for the broken-feed alert
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 90


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.seen: dict[str, str] = data.get("seen", {})
        self.sent: dict[str, str] = data.get("sent", {})
        self.years: dict[str, list] = data.get("years", {})
        self.ai: dict[str, list] = data.get("ai", {})
        self.usage: dict = data.get("usage", {})
        self.feeds: dict[str, dict] = data.get("feeds", {})
        self.is_bootstrap = not self.seen

    # ---- AI cache + daily budget
    def cached_ai(self, key: str) -> dict | None:
        v = self.ai.get(key)
        return v[0] if v else None

    def cache_ai(self, key: str, result: dict) -> None:
        self.ai[key] = [result, date.today().isoformat()]

    def _today_usage(self) -> dict:
        today = date.today().isoformat()
        if self.usage.get("date") != today:
            self.usage = {"date": today, "calls": 0, "usd": 0.0}
        return self.usage

    def ai_calls_today(self) -> int:
        return self._today_usage().get("calls", 0)

    def ai_usd_today(self) -> float:
        return self._today_usage().get("usd", 0.0)

    def count_ai_call(self, cost_usd: float = 0.0) -> None:
        u = self._today_usage()
        u["calls"] = u.get("calls", 0) + 1
        u["usd"] = round(u.get("usd", 0.0) + cost_usd, 6)

    def mark_seen(self, key: str) -> None:
        self.seen.setdefault(key, date.today().isoformat())

    def mark_sent(self, dedupe_key: str) -> None:
        self.sent.setdefault(dedupe_key, date.today().isoformat())

    def cached_years(self, key: str):
        """Returns (hit, value)."""
        v = self.years.get(key)
        return (True, v[0]) if v else (False, None)

    def cache_years(self, key: str, value: int | None) -> None:
        self.years[key] = [value, date.today().isoformat()]

    def save(self) -> None:
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        self.sent = {k: v for k, v in self.sent.items() if v >= cutoff}
        self.years = {k: v for k, v in self.years.items() if v[1] >= cutoff}
        self.ai = {k: v for k, v in self.ai.items() if v[1] >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seen": self.seen, "sent": self.sent, "years": self.years, "ai": self.ai,
                                   "usage": self.usage, "feeds": self.feeds},
                                  indent=0, sort_keys=True), "utf-8")
        tmp.replace(self.path)
