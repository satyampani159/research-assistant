"""D2.4 — numeric_check must catch adversarially plausible wrong numbers.

The bar set in implementation.md: catch >= 8/10 corrupted claims.
"""

from __future__ import annotations

from src.quality import extract_numbers, numeric_check

SO_URL = "https://stackoverflow.blog/2025/07/developer-survey-2025-ai-results/"
ARXIV_URL = "https://arxiv.org/abs/2603.01234"
REUTERS_URL = "https://www.reuters.com/technology/enterprise-adoption-coding-agents-2026-03-11/"
TC_URL = "https://techcrunch.com/2026/05/12/coding-agents-enterprise-pricing/"
DOCS_URL = "https://docs.anthropic.com/en/docs/claude-code/overview"
EU_URL = "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai"


def page(url: str) -> str:
    from src.tools import fetch_page

    return fetch_page(url)["text"]


# --- honest claims: figures present in the source ----------------------------

HONEST = [
    ("84% of professional developers are using or planning to use AI coding tools", SO_URL),
    ("the survey gathered responses from 49,000 developers", SO_URL),
    ("the strongest system completed 41% of tasks end-to-end", ARXIV_URL),
    ("12 agentic coding systems were evaluated", ARXIV_URL),
    ("adoption of coding agents reached 38%, up from 21%", REUTERS_URL),
    ("annual spend grew to $1.2B", TC_URL),
    ("Claude Code works with a 200,000 token context window", DOCS_URL),
    ("high-risk obligations apply from 2 August 2026", EU_URL),
]


def test_honest_claims_match():
    for claim, url in HONEST:
        result = numeric_check(claim, page(url))
        assert result["match"], f"should match: {claim} -> {result['note']}"


# --- adversarially plausible wrong numbers: 10 cases, all must be caught -----

CORRUPT = [
    # same magnitude, wrong value — the hardest kind
    ("64% of professional developers are using or planning to use AI coding tools", SO_URL),
    ("19,000 developers responded to the survey", SO_URL),
    ("the strongest system completed 71% of tasks end-to-end", ARXIV_URL),
    ("adoption of coding agents reached 83%, up from 21%", REUTERS_URL),
    # scale confusion: $1.2B vs $1.2M
    ("annual spend grew to $1.2M", TC_URL),
    # off-by-a-power context window
    ("Claude Code works with a 20,000 token context window", DOCS_URL),
    # digit-level corruption of a date
    ("high-risk obligations apply from 2 August 2025", EU_URL),
    # plausible but invented figure
    ("developers accepted just over 55% of Copilot's code suggestions",
     "https://github.blog/news-insights/research/research-quantifying-github-copilots-impact-on-developer-productivity-and-happiness/"),
    # right number, wrong companion number
    ("adoption reached 38%, up from 44%", REUTERS_URL),
    # unit swap: dollars where percent belongs
    ("adoption of coding agents reached $38%, up from 21%", REUTERS_URL),
]


def test_corrupted_claims_are_caught():
    caught = 0
    for claim, url in CORRUPT:
        if not numeric_check(claim, page(url))["match"]:
            caught += 1
    assert caught >= 8, f"only caught {caught}/10 corrupted claims"


def test_scale_confusion_is_caught():
    result = numeric_check("annual spend grew to $1.2M", page(TC_URL))
    assert result["claim_numbers"] == ["$1200000"]
    assert "$1.2M" not in result["note"]


def test_scale_normalisation_makes_equivalent_forms_match():
    source = "Costs reached 1.2B dollars in total spend."
    for claim in ["costs hit $1,200,000,000", "costs hit 1200 M", "costs hit 1.2B"]:
        assert numeric_check(claim, source)["match"], claim


def test_no_numbers_in_claim_always_matches():
    result = numeric_check("Adoption is accelerating across large employers.", page(REUTERS_URL))
    assert result["match"]
    assert result["claim_numbers"] == []
    assert "no numeric" in result["note"]


def test_empty_source_cannot_match():
    result = numeric_check("adoption reached 38%", "")
    assert not result["match"]
    assert "no figures" in result["note"]


def test_shape_is_frozen():
    result = numeric_check("adoption reached 38%", page(REUTERS_URL))
    assert set(result) == {"match", "claim_numbers", "source_numbers", "note"}
    assert isinstance(result["match"], bool)
    assert all(isinstance(n, str) for n in result["claim_numbers"])


def test_years_are_not_measurements():
    # 2026 on its own is prose, not a figure the source must corroborate
    assert extract_numbers("The report was published in 2026") == []
    assert "%2026" not in extract_numbers("published in 2026")


def test_percent_and_currency_units_are_preserved():
    numbers = extract_numbers("38% adoption and $480 per month")
    assert "%38" in numbers
    assert any(n.startswith("$") for n in numbers)


def test_digit_level_corruption_is_detected():
    """The strongest signal: same length, different digits."""
    src = page(REUTERS_URL)
    assert numeric_check("adoption reached 38%", src)["match"]
    assert not numeric_check("adoption reached 39%", src)["match"]
    assert not numeric_check("adoption reached 33%", src)["match"]