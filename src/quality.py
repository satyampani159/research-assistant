"""Deterministic quality layer: numeric_check, verify_claim, 4 guardrails.

Rule of the house (§2.10 decision 3): numbers are checked by Python before any
LLM is asked for an opinion. The LLM entailment hook is a single swappable
function so the crews can inject a real model while tests stay offline.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Callable

from .tools import domain_of, fetch_page


def _s(value) -> str:
    """Coerce crew/LLM outputs to text.

    Agents occasionally hand dicts (e.g. {claim_id, statement}) where a
    string was promised; re/urlparse on a dict raises
    "expected string or bytes-like object, got 'dict'" and kills the
    editorial run. Extract the most plausible text instead of crashing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("statement", "text", "claim", "body", "title", "url", "quote"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        return str(value)
    return str(value)

# ------------------------------------------------------------- numbers -----

_SCALES = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
_MONEY = {"$": 1.0, "usd": 1.0, "€": 1.0, "£": 1.0}

# A number, optional scale suffix, optional currency, optional % sign.
_NUM_RE = re.compile(
    r"(?P<cur>[$€£])?\s*(?P<val>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<scale>[kKmMbBtT])?(?P<pct>%)?"
)
# Years and bare small integers are usually prose, not measurements.
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _fmt(value: float) -> str:
    """Canonical number text. Never scientific notation — '$1.2e+06' would
    silently fail to compare equal to '$1200000'."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-"} else "0"


def extract_numbers(text: str) -> list[str]:
    """Pull figures out of text, normalised to base units.

    '1.2M' / '$1,200,000' / '45%' all collapse to a canonical string so that
    scale and formatting differences are not read as contradictions.
    """
    if not text:
        return []
    found: list[str] = []
    for m in _NUM_RE.finditer(text):
        val = _to_float(m.group("val"))
        if val is None:
            continue
        raw = m.group("val")
        if _YEAR_RE.match(raw) and not (m.group("pct") or m.group("cur") or m.group("scale")):
            continue  # bare year: not a measurement
        if val == 0 and not m.group("scale"):
            continue
        scale = _SCALES.get((m.group("scale") or "").lower(), 1.0)
        if m.group("pct"):
            unit = "%"
        elif m.group("cur"):
            unit = m.group("cur")
        elif m.group("scale"):
            unit = m.group("scale").lower()
        else:
            unit = ""
        norm = val * scale
        found.append(f"{unit}{_fmt(norm)}")
    # de-dup, order-preserving
    seen: set[str] = set()
    return [n for n in found if not (n in seen or seen.add(n))]


# A bare year is usually prose, but a year inside a date is a checkable fact.
# These patterns exist so "2 August 2025" cannot pass against a source that
# says "2 August 2026".
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DATE_PATTERNS = [
    (re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})\b", re.I), ("d", "m", "y")),
    (re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), ("m", "d", "y")),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("y", "m", "d")),
]


def extract_dates(text: str) -> list[str]:
    """Full dates as YYYY-MM-DD, order-preserved. Format-variant tolerant."""
    if not text:
        return []
    months = {}
    for i, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    ):
        months[name] = i
    found: list[str] = []
    for pattern, order in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            parts = {"y": m.group(1), "m": m.group(2), "d": m.group(3)}
            year, month, day = parts["y"], parts["m"], parts["d"]
            if order == ("d", "m", "y"):
                day, month = m.group(1), m.group(2)
                year = m.group(3)
            elif order == ("m", "d", "y"):
                month, day = m.group(1), m.group(2)
                year = m.group(3)
            if not month.isdigit():
                month = str(months.get(month[:3].lower(), 0))
            if month == "0":
                continue
            iso = f"{year}-{int(month):02d}-{int(day):02d}"
            if iso not in found:
                found.append(iso)
    return found


def _split_number(token: str) -> tuple[float, str]:
    """Canonical '1200000' -> (1200000.0, 'cur') | '%84' -> (84.0, 'pct') | ..."""
    if token.startswith("%"):
        return float(token[1:]), "pct"
    if token[0] in "$€£":
        return float(token[1:]), "cur"
    mag = re.match(r"^([kmbt])(.+)$", token)
    if mag:
        return float(mag.group(2)), "mag"
    return float(token), "raw"


_REL_TOL = 5e-3  # tolerate "just over 30%" vs "30%", but never 38 vs 39


def _same_number(a: str, b: str) -> bool:
    """Equal magnitude, and dimensions that do not conflict.

    A magnitude suffix (1.2B) is compatible with a currency rendering of the
    same amount ($1,200,000,000). A percentage is NOT compatible with a bare
    count: '$38%' against '38' is a unit swap, not agreement.
    """
    av, au = _split_number(a)
    bv, bu = _split_number(b)
    if not math.isclose(av, bv, rel_tol=_REL_TOL, abs_tol=1e-9):
        return False
    return au == bu or "mag" in (au, bu)


def numeric_check(claim: str, source_text: str) -> dict:
    """{match, claim_numbers, source_numbers, note} — pure Python, no LLM.

    match is True when the claim makes no numeric assertion, or when every
    figure *and* every full date in the claim is present in the source text.
    """
    claim, source_text = _s(claim), _s(source_text)
    claim_numbers = extract_numbers(claim)
    source_numbers = extract_numbers(source_text)
    source_dates = extract_dates(source_text)
    result = {
        "match": False,
        "claim_numbers": claim_numbers,
        "source_numbers": source_numbers,
        "note": "",
    }

    if not claim_numbers and not extract_dates(claim):
        result["match"] = True
        result["note"] = "claim contains no numeric assertion"
        return result

    if claim_numbers and not source_numbers and not source_dates:
        result["note"] = "source text contains no figures to compare against"
        return result

    missing = [n for n in claim_numbers if not any(_same_number(n, s) for s in source_numbers)]
    if missing:
        result["note"] = f"claim figures absent from source: {', '.join(missing)}"
        return result

    missing_dates = [d for d in extract_dates(claim) if d not in source_dates]
    if missing_dates:
        result["note"] = f"claim dates absent from source: {', '.join(missing_dates)}"
        return result

    bits = []
    if claim_numbers:
        bits.append(f"{len(claim_numbers)} figure(s)")
    found_dates = extract_dates(claim)
    if found_dates:
        bits.append(f"{len(found_dates)} date(s)")
    result["match"] = True
    result["note"] = "all " + ", ".join(bits) + " found in source"
    return result


# ----------------------------------------------------------- entailment -----

_ENTAILMENT_FN: Callable[[str, str], tuple[bool, str]] | None = None


def set_entailment_fn(fn: Callable[[str, str], tuple[bool, str]] | None) -> None:
    """Inject the LLM entailment check (crews pass one in; tests pass none)."""
    global _ENTAILMENT_FN
    _ENTAILMENT_FN = fn


def lexical_entailment(claim: str, source_text: str) -> tuple[bool, str]:
    """Offline default: stemmed word overlap + verbatim n-gram match.

    Deliberately conservative, but not so brittle that a short factual claim
    ("41% of tasks completed") fails purely on inflection.
    """
    claim, source_text = _s(claim), _s(source_text)
    if _has_verbatim_span(claim, source_text, size=5):
        return True, "claim phrase appears verbatim in source"

    claim_stems = _stems(claim)
    source_stems = _stems(source_text)
    if not claim_stems:
        return True, "claim has no content words"
    overlap = len(claim_stems & source_stems) / len(claim_stems)

    # Figures carry a numeric claim; prose carry a qualitative one.
    threshold = 0.4 if _NUM_RE.search(claim) else 0.6
    ok = overlap >= threshold
    return ok, f"lexical overlap {overlap:.0%} (threshold {threshold:.0%})"


_STOPWORDS = frozenset(
    "the a an of and to in for on is are was were that this with as by at from it its be "
    "has have had we our you their they will was were been being than then when which who".split()
)


def _stems(text: str) -> set[str]:
    """Content words reduced to 5-char stems so inflections collapse together."""
    return {
        w[:5]
        for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if w not in _STOPWORDS
    }


def _has_verbatim_span(claim: str, source_text: str, size: int = 5) -> bool:
    """True if any `size`-word window of the claim appears verbatim in the source."""
    words = re.findall(r"[a-z0-9]+", (claim or "").lower())
    source = re.sub(r"[^a-z0-9 ]", " ", (source_text or "").lower())
    source = re.sub(r"\s+", " ", source).strip()
    for i in range(len(words) - size + 1):
        span = " ".join(words[i:i + size])
        if span and span in source:
            return True
    return False


def entailment(claim: str, source_text: str) -> tuple[bool, str]:
    """Swappable: LLM hook if injected, else deterministic lexical fallback."""
    if _ENTAILMENT_FN is not None:
        return _ENTAILMENT_FN(claim, source_text)
    return lexical_entailment(claim, source_text)


# ----------------------------------------------------------- verify_claim ---

def verify_claim(claim_id: str, text: str, url: str) -> dict:
    """Re-fetch a source and judge one claim.

    Returns a ClaimResult-shaped dict:
      VERIFIED     figures and dates match AND the page entails the claim
      CONTRADICTED figures differ on a claim the page is actually about
      UNSUPPORTED  the page does not entail the claim (figure mismatch included)
      STALE        the source could not be re-fetched at all
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    claim_id = _s(claim_id) or "C?"
    text, url = _s(text), _s(url)
    base = {
        "claim_id": claim_id,
        "confidence": 0.0,
        "evidence_quote": "",
        "checked_url": url,
        "checked_at": now,
    }

    try:
        page = fetch_page(url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure means STALE
        return {**base, "verdict": "STALE", "confidence": 0.0,
                "evidence_quote": f"fetch failed: {exc}"}

    source_text = page.get("text", "")
    if not source_text.strip():
        return {**base, "verdict": "STALE", "evidence_quote": "empty page text"}

    num = numeric_check(text, source_text)
    quote = best_quote(text, source_text)
    entailed, why = entailment(text, source_text)

    if not num["match"]:
        # Numbers gate first: a wrong figure is never VERIFIED, whatever the
        # entailment model says. Entailment then separates "the source states
        # something different" from "the source never says this".
        if entailed:
            return {**base, "verdict": "CONTRADICTED", "confidence": 0.1,
                    "evidence_quote": quote or num["note"]}
        return {**base, "verdict": "UNSUPPORTED", "confidence": 0.3,
                "evidence_quote": quote or f"{num['note']}; {why}"}

    if not entailed:
        return {**base, "verdict": "UNSUPPORTED", "confidence": 0.3,
                "evidence_quote": quote or why}

    return {**base, "verdict": "VERIFIED", "confidence": 0.9, "evidence_quote": quote}


def best_quote(claim: str, source_text: str, width: int = 240) -> str:
    """Longest sentence-ish window of the source that shares words with the claim."""
    claim, source_text = _s(claim), _s(source_text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source_text) if s.strip()]
    if not sentences:
        return source_text[:width]
    words = set(re.findall(r"[a-z0-9]{4,}", claim.lower()))
    best, best_score = "", 0
    for s in sentences:
        score = len(words & set(re.findall(r"[a-z0-9]{4,}", s.lower())))
        if score > best_score:
            best, best_score = s, score
    return best[:width]


# ------------------------------------------------------------ guardrails ----
# Each returns (bool, message) so CrewAI's guardrail callback contract is met.

MIN_DOMAINS_PER_SECTION = 3
MIN_CITATION_COVERAGE = 0.8


def min_sources_per_section(sections: list[dict], min_domains: int = MIN_DOMAINS_PER_SECTION):
    """§2.9.1 — every section draws on >= N distinct domains."""
    if not sections:
        return True, "no sections to check"
    offenders = []
    for sec in sections:
        if not isinstance(sec, dict):
            offenders.append(f"<malformed section {type(sec).__name__}>")
            continue
        domains = {domain_of(u) for u in sec.get("sources", []) if _s(u)}
        domains.discard("")
        if len(domains) < min_domains:
            heading = sec.get("heading", "<unnamed>")
            offenders.append(f"{heading} ({len(domains)}/{min_domains})")
    if offenders:
        return False, f"sections below {min_domains} distinct domains: {'; '.join(offenders)}"
    return True, f"all {len(sections)} sections cite >= {min_domains} domains"


def all_claims_have_ids(draft: dict):
    """§2.9.2 — no orphan assertions: cited_claims must hold real ids."""
    if isinstance(draft, dict) and "sections" in draft:
        cited: list[str] = []
        for sec in draft.get("sections", []):
            if isinstance(sec, dict):
                cited.extend(sec.get("cited_claims", []) or [])
    else:
        cited = list(draft or [])
    orphans = [c for c in cited if not isinstance(c, str) or not c.strip()]
    if orphans:
        return False, f"{len(orphans)} claim id(s) empty or malformed: {orphans[:5]}"
    return True, f"{len(cited)} claim id(s) present and well-formed"


def citation_coverage(cited: list[str], total: int, threshold: float = MIN_CITATION_COVERAGE):
    """§2.9.3 — cited / total claims >= threshold."""
    if total <= 0:
        return True, "no claims to cover"
    # Coerce defensively: the writer occasionally emits dicts, which are unhashable.
    keys = [(c if isinstance(c, str) else str(getattr(c, "claim_id", c))).strip().lower() for c in cited]
    ratio = len(set(keys)) / total
    if ratio < threshold:
        return False, f"citation coverage {ratio:.0%} below {threshold:.0%} ({len(set(keys))}/{total})"
    return True, f"citation coverage {ratio:.0%} ({len(set(keys))}/{total})"


_SENTENCE_RE = re.compile(r"[^.!?\n]*\d[^.!?\n]*[.!?]?")
_CITATION_RE = re.compile(r"\[\d+\]")


def no_uncited_numbers(body: str):
    """§2.9.4 — any digit in prose must live in a sentence carrying [Sn]."""
    offenders = []
    for match in _SENTENCE_RE.finditer(_s(body) or ""):
        sent = match.group(0).strip()
        if len(sent) < 2 or not re.search(r"\d", sent):
            continue
        if _CITATION_RE.search(sent):
            continue
        # section headings and reference lines legitimately carry digits
        if sent.startswith("#") or sent.startswith("|"):
            continue
        offenders.append(sent[:80])
    if offenders:
        return False, f"{len(offenders)} sentence(s) with uncited numbers: {offenders[:3]}"
    return True, "every numeric sentence carries a [Sn] citation"


GUARDRAILS = {
    "min_sources_per_section": min_sources_per_section,
    "all_claims_have_ids": all_claims_have_ids,
    "citation_coverage": citation_coverage,
    "no_uncited_numbers": no_uncited_numbers,
}


__all__ = [
    "extract_numbers",
    "extract_dates",
    "numeric_check",
    "set_entailment_fn",
    "entailment",
    "lexical_entailment",
    "verify_claim",
    "best_quote",
    "min_sources_per_section",
    "all_claims_have_ids",
    "citation_coverage",
    "no_uncited_numbers",
    "GUARDRAILS",
]