from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str            # adapter name, e.g. "greenhouse"
    source_id: str         # id unique within the source
    company: str
    title: str
    url: str
    location: str = ""
    posted_at: datetime | None = None   # timezone-aware UTC when known
    description: str = ""               # plain text
    # Filled during the pipeline:
    min_years: int | None = None
    years_basis: str = ""               # "regex" | "llm" | "curated" | "not stated"
    sponsorship: str = ""               # Simplify provides this
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def dedupe_key(self) -> str:
        return f"{norm(self.company)}|{norm(self.title)}"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(s: str) -> str:
    """Greenhouse returns HTML (sometimes double-escaped); flatten to plain text."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"(?i)<\s*(br|/p|/li|/div|/h\d)\s*/?>", "\n", s)
    s = _TAG.sub(" ", s)
    s = _WS.sub(" ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()
