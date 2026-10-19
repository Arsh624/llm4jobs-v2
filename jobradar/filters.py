"""Cheap, deterministic filters. These run before any LLM call."""
from __future__ import annotations

import re

# ------------------------------------------------------------------ seniority
SENIOR = re.compile(
    r"\b(senior|sr|staff|principal|lead|leader|manager|director|head|vp|svp|evp|"
    r"vice\s+president|chief|architect|distinguished|fellow|partner|president|"
    r"expert|iii|iv|level\s*[3-9]|l[5-9])\b", re.I)
# "Associate/Rotational Product Manager" and similar are entry-level despite "manager".
JUNIOR_MANAGER = re.compile(r"\b(associate|rotational|apm|new\s*grad|university|early\s*career)\b.*\bmanager\b", re.I)


def is_senior(title: str) -> bool:
    t = title.replace("Sr.", "Sr ")
    if JUNIOR_MANAGER.search(t):
        t = re.sub(r"(?i)\bmanager\b", "", t)
    return bool(SENIOR.search(t))


INTERN = re.compile(
    r"\b(intern|internship|co-?op|apprentice(ship)?|undergraduates?)\b"
    r"|\b(summer|fall|winter|spring)\s+20\d\d\b", re.I)   # "Spring 2027" = term-based co-op/intern


def is_intern(title: str) -> bool:
    return bool(INTERN.search(title))


def matches_role(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    t = title.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(k.lower())}(?![a-z])", t) for k in keywords)


# ------------------------------------------------------------------- location
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC"}
US_WORDS = re.compile(
    r"united states|\busa\b|\bu\.s\.?\b|\bus\b|\bamericas?\b|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
    r"hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
    r"michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    r"new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    r"wisconsin|wyoming|san francisco|seattle|bay area|silicon valley|los angeles|boston|chicago|"
    r"austin|denver|boulder|atlanta|miami|dallas|houston|palo alto|mountain view|menlo park|"
    r"sunnyvale|san jose|santa clara|redmond|bellevue|cupertino|san diego|pittsburgh|"
    r"philadelphia|phoenix|portland|nyc|brooklyn|salt lake", re.I)
NON_US = re.compile(
    r"canada|toronto|vancouver|montreal|ontario|london|united kingdom|\buk\b|england|ireland|dublin|"
    r"india|bangalore|bengaluru|hyderabad|pune|chennai|gurgaon|noida|mumbai|delhi|germany|berlin|munich|"
    r"france|paris|netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|krakow|japan|tokyo|"
    r"singapore|australia|sydney|melbourne|brazil|s[aã]o paulo|mexico|israel|tel aviv|china|beijing|"
    r"shanghai|shenzhen|hong kong|korea|seoul|taiwan|taipei|philippines|manila|colombia|bogot|"
    r"argentina|buenos aires|costa rica|switzerland|zurich|sweden|stockholm|denmark|copenhagen|"
    r"italy|milan|portugal|lisbon|romania|bucharest|czech|prague|hungary|budapest|serbia|belgrade|"
    r"emea|apac|latam|europe|asia|uae|dubai|vietnam|thailand|indonesia|malaysia|new zealand|"
    r"chile|peru|uruguay|estonia|lithuania|ukraine|turkey|istanbul|egypt|nigeria|kenya|south africa|"
    r"saudi|riyadh|jeddah|king abdullah|\bksa\b|qatar|doha|abu dhabi|kuwait|bahrain|oman|jordan|"
    r"pakistan|bangladesh|sri lanka|morocco|greece|athens|austria|vienna|belgium|brussels|finland|"
    r"helsinki|norway|oslo|scotland|edinburgh|wales|luxembourg|slovakia|croatia|bulgaria|cyprus|malta",
    re.I)
_STATE_ABBR = re.compile(r"(?:,|\b)\s*([A-Z]{2})\b")
_SPLIT = re.compile(r";|\||/| or |\n")


def _segment_is_us(seg: str, remote_counts: bool) -> bool | None:
    seg = seg.strip()
    if not seg:
        return None
    if NON_US.search(seg) and not re.search(r"united states|\busa\b", seg, re.I):
        return False
    if US_WORDS.search(seg) or any(m in US_STATES for m in _STATE_ABBR.findall(seg)):
        return True
    if "remote" in seg.lower():
        return remote_counts
    return None


def is_us(location: str, remote_counts: bool = True) -> bool:
    """True if any listed location is in the US. Unknown/empty locations are kept."""
    verdicts = [_segment_is_us(s, remote_counts) for s in _SPLIT.split(location or "")]
    if any(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    return True


# ---------------------------------------------------------------------- years
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}|" + "|".join(WORDNUM) + r")"
YEARS = re.compile(
    rf"(?:at\s+least|minimum\s+(?:of\s+)?|min\.?\s+)?{_NUM}\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*{_NUM}\s*)?"
    rf"\+?\s*(?:years?|yrs?)", re.I)
EXPERIENCE_CTX = re.compile(r"experience|exp\b|professional|hands-on", re.I)


def _n(s: str) -> int:
    return int(s) if s.isdigit() else WORDNUM[s.lower()]


def extract_min_years(text: str) -> int | None:
    """Smallest 'N years' requirement that appears in an experience context.

    "5+ years ... or 3+ years with a PhD" -> 3;  "0-2 years of experience" -> 0.
    Returns None when nothing is stated.
    """
    found = []
    for m in YEARS.finditer(text or ""):
        before, after = text[max(0, m.start() - 40): m.start()], text[m.end(): m.end() + 60]
        # "3+ years of experience" / "Experience: 3+ years" -- but not "over 20 years in business"
        if not (EXPERIENCE_CTX.search(after) or re.search(r"experience", before, re.I)):
            continue
        window = before + m.group(0) + after
        if re.search(r"years?\s+old|year\s+(?:degree|program)|years?\s+of\s+(?:college|university|age)", window, re.I):
            continue
        n = _n(m.group(1))
        if n <= 30:
            found.append(n)
    return min(found) if found else None
