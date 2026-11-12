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


# Unambiguous seniority words: always dropped. The rest of SENIOR ("lead", "staff", "manager",
# "head", "architect", "principal", ...) can be misleading, so those titles get an AI second look.
STRONG_SENIOR = re.compile(
    r"\b(senior|sr|director|vp|svp|evp|vice\s+president|chief|distinguished|fellow|president|"
    r"partner|iii|iv|level\s*[3-9]|l[5-9]|smts|lmts|pmts)\b", re.I)   # Senior/Lead/Principal MTS


def _normalize_title(title: str) -> str:
    t = title.replace("Sr.", "Sr ")
    # "Member of Technical Staff" is the standard all-levels title at AI labs, not a Staff-level role.
    t = re.sub(r"(?i)\bmembers?\s+of\s+(the\s+)?technical\s+staff\b", "MTS", t)
    if JUNIOR_MANAGER.search(t):
        t = re.sub(r"(?i)\bmanager\b", "", t)
    return t


def senior_strength(title: str) -> str | None:
    """'strong' (definitely senior), 'weak' (ambiguous word only) or None (not senior)."""
    t = _normalize_title(title)
    if STRONG_SENIOR.search(t):
        return "strong"
    return "weak" if SENIOR.search(t) else None


def is_senior(title: str) -> bool:
    return senior_strength(title) is not None


# Season-term titles ("New Grad, Summer 2027") are kept on purpose: many are full-time start dates.
INTERN = re.compile(r"\b(intern|internship|co-?op|apprentice(ship)?|undergraduates?)\b", re.I)


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


def us_status(location: str, remote_counts: bool = True) -> bool | None:
    """True = a US location is listed, False = only non-US locations, None = can't tell
    ("2 Locations", empty, bare "Remote" when remote_counts is False)."""
    verdicts = [_segment_is_us(s, remote_counts) for s in _SPLIT.split(location or "")]
    if any(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    return None


def is_us(location: str, remote_counts: bool = True) -> bool:
    """True if any listed location is in the US. Unknown/empty locations are kept."""
    return us_status(location, remote_counts) is not False


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


# ------------------------------------------------------------ employment type
CONTRACT_TITLE = re.compile(
    r"\b(contract(?:or)?|contingent|temporary|temp|fixed[\s-]*term|consultant|c2h|"
    r"contract[\s-]*to[\s-]*(?:hire|perm(?:anent)?))\b", re.I)
CTH = re.compile(r"contract[\s-]*to[\s-]*(?:hire|perm(?:anent)?)|\bc2h\b|temp[\s-]*to[\s-]*(?:hire|perm)", re.I)
CONTRACT_BODY = re.compile(
    r"\b(?:this|the)\s+(?:is\s+a\s+|position\s+is\s+a\s+|role\s+is\s+a\s+)?(?:\d+[\s-]*month\s+)?contract\b|"
    r"\bcontract\s+(?:position|role|opportunity|assignment|duration|length)\b|"
    r"\b\d+[\s-]*(?:month|mo)\s+(?:contract|assignment)\b|\bw[\s-]?2\s+contract\b|"
    r"\bcontract\s*[:\-]\s*\d+|\bassignment\s+length\b|\bemployment\s+type\s*[:\-]\s*(?:contract|temporary)", re.I)
PART_TIME = re.compile(r"\bpart[\s-]*time\b", re.I)
C2C_1099 = re.compile(r"\b(c2c|corp[\s-]*to[\s-]*corp|1099|independent\s+contractor)\b", re.I)
W2 = re.compile(r"\bw[\s-]?2\b", re.I)


def employment_type(title: str, description: str = "", hint: str = "") -> str:
    """'contract', 'contract-to-hire', 'part-time' or 'full-time'. `hint` is the job system's own
    employment field when it has one (Ashby employmentType, Lever commitment, ...)."""
    h = (hint or "").lower()
    head = f"{title}\n{description[:1500]}"
    if CTH.search(head) or "contract to hire" in h:
        return "contract-to-hire"
    if any(w in h for w in ("contract", "temporary", "contingent", "freelance", "fixed")):
        return "contract"
    if re.search(r"part[\s-]*time", h):
        return "part-time"
    if CONTRACT_TITLE.search(title) and not re.search(r"consultant", title, re.I):
        return "contract"
    if CONTRACT_BODY.search(head):
        return "contract"
    if PART_TIME.search(title):
        return "part-time"
    return "full-time"


def is_c2c_only(text: str) -> bool:
    """Corp-to-corp / 1099 postings (self-employed contractors): no sponsorship, not OPT-compatible.
    A posting that also says W-2 is fine."""
    return bool(C2C_1099.search(text or "")) and not W2.search(text or "")


NO_SPONSOR = re.compile(
    r"(?:no|not|unable\s+to|cannot|can[\s']*t|won[\s']*t|will\s+not|do(?:es)?\s+not|without)\s+"
    r"(?:\w+\s+){0,4}?(?:sponsor(?:ship)?|visa)|"
    r"(?:sponsor(?:ship)?)\s+(?:is\s+)?(?:not\s+(?:available|offered|provided)|unavailable)|"
    r"(?:must|should)\s+be\s+(?:legally\s+)?(?:authori[sz]ed|eligible)\s+to\s+work[^.]{0,80}without\s+(?:the\s+need\s+for\s+)?"
    r"(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship|"
    r"u\.?s\.?\s+(?:citizens?|persons?)\s+(?:only|required)|us\s+citizenship\s+(?:is\s+)?required|"
    r"must\s+be\s+a\s+u\.?s\.?\s+(?:citizen|person)", re.I)
YES_SPONSOR = re.compile(
    r"(?:will|can|may|able\s+to|happy\s+to|do|does)\s+(?:\w+\s+){0,3}?sponsor(?:ship)?\b|"
    r"(?:visa|h[\s-]?1[\s-]?b|opt|stem|cpt)\s+(?:\w+\s+){0,3}?(?:sponsorship\s+)?(?:is\s+|are\s+)?(?:available|supported|offered|welcome|accepted|ok)|"
    r"sponsorship\s+(?:is\s+)?(?:available|offered|provided)|h[\s-]?1[\s-]?b\s+(?:transfer|sponsor)|"
    r"open\s+to\s+(?:candidates\s+on\s+)?(?:opt|stem|visa)", re.I)


def sponsorship_hint(text: str) -> str:
    """'no sponsorship' | 'sponsorship mentioned' | '' (not stated). Free text rules, no AI."""
    t = re.sub(r"\s+", " ", text or "")
    if NO_SPONSOR.search(t):
        return "no sponsorship"
    if YES_SPONSOR.search(t):
        return "sponsorship mentioned"
    return ""


def simplify_sponsorship(label: str) -> str:
    """Map SimplifyJobs' sponsorship labels to our wording ('Other' / unknown -> not stated)."""
    t = (label or "").lower()
    if "not offer" in t or "no sponsor" in t:
        return "no sponsorship"
    if "citizen" in t:
        return "US citizenship required"
    if "offer" in t:
        return "sponsorship mentioned"
    return ""
