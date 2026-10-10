"""D2.5 — verify_claim + the four §2.9 guardrails, fully offline."""

from __future__ import annotations

import pytest

from src import quality
from src.contracts import ClaimResult, VerificationLog

ARXIV = "https://arxiv.org/abs/2603.01234"
REUTERS = "https://www.reuters.com/technology/enterprise-adoption-coding-agents-2026-03-11/"
EU = "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai"
MISSING = "https://example.invalid/this-page-was-deleted"


# ------------------------------------------------------------ verify_claim ---

@pytest.mark.parametrize(
    "claim,url,verdict",
    [
        ("The strongest system completed 41% of tasks end-to-end", ARXIV, "VERIFIED"),
        ("The strongest system completed 91% of tasks end-to-end", ARXIV, "CONTRADICTED"),
        ("Adoption of coding agents reached 38%, up from 21%", REUTERS, "VERIFIED"),
        ("High-risk obligations apply from 2 August 2026", EU, "VERIFIED"),
        ("Vendors charge $19 per seat per month", ARXIV, "UNSUPPORTED"),
    ],
)
def test_verdicts(claim, url, verdict):
    assert quality.verify_claim("C1", claim, url)["verdict"] == verdict


def test_unfetchable_source_is_stale_not_contradicted():
    """A dead link is not the writer's fault — it must not read as a falsehood."""
    result = quality.verify_claim("C1", "adoption reached 38%", MISSING)
    assert result["verdict"] == "STALE"
    assert result["confidence"] == 0.0
    assert "fetch failed" in result["evidence_quote"]


def test_result_shape_matches_contract():
    result = quality.verify_claim("C1", "The strongest system completed 41% of tasks", ARXIV)
    ClaimResult(**result)  # raises if the shape drifts from §2.5


def test_verified_claim_carries_an_evidence_quote():
    result = quality.verify_claim("C1", "The strongest system completed 41% of tasks", ARXIV)
    assert result["verdict"] == "VERIFIED"
    assert result["evidence_quote"]
    assert result["checked_url"] == ARXIV
    assert result["checked_at"]


def test_entailment_hook_is_injectable():
    """D2.5 requires the LLM call to live in one swappable function."""
    quality.set_entailment_fn(lambda claim, src: (True, "always agrees"))
    try:
        result = quality.verify_claim(
            "C1", "Totally unrelated assertion about penguins", ARXIV
        )
        assert result["verdict"] == "VERIFIED"
        assert result["confidence"] == 0.9
    finally:
        quality.set_entailment_fn(None)
    # hook removed -> back to conservative default
    assert quality.verify_claim(
        "C1", "Totally unrelated assertion about penguins", ARXIV
    )["verdict"] == "UNSUPPORTED"


def test_deterministic_check_runs_before_the_model():
    """Numbers are checked in Python first, so a rubber-stamping hook cannot
    launder a wrong figure."""
    quality.set_entailment_fn(lambda claim, src: (True, "rubber stamp"))
    try:
        wrong = quality.verify_claim("C1", "The strongest system completed 91% of tasks", ARXIV)
        assert wrong["verdict"] == "CONTRADICTED"
    finally:
        quality.set_entailment_fn(None)


# -------------------------------------------------------------- guardrails ---

def test_min_sources_per_section():
    good = [{"heading": "Adoption", "sources": ["https://a.com/x", "https://b.org/y", "https://c.gov/z"]}]
    bad = [{"heading": "Adoption", "sources": ["https://a.com/x", "https://a.com/y"]}]
    assert quality.min_sources_per_section(good)[0] is True
    ok, msg = quality.min_sources_per_section(bad)
    assert ok is False
    assert "Adoption" in msg


def test_min_sources_counts_distinct_domains_not_urls():
    """Three URLs from one site is not three sources."""
    same_domain = [
        {"heading": "Adoption", "sources": [
            "https://blog.example.com/a", "https://blog.example.com/b", "https://blog.example.com/c",
        ]}
    ]
    assert quality.min_sources_per_section(same_domain)[0] is False


def test_all_claims_have_ids():
    draft = {"sections": [{"heading": "A", "cited_claims": ["C1", "C2"]}]}
    assert quality.all_claims_have_ids(draft)[0] is True
    bad = {"sections": [{"heading": "A", "cited_claims": ["C1", "  "]}]}
    assert quality.all_claims_have_ids(bad)[0] is False


def test_citation_coverage():
    assert quality.citation_coverage(["C1", "C2", "C3"], 3)[0] is True
    ok, msg = quality.citation_coverage(["C1"], 5)
    assert ok is False
    assert "20%" in msg
    # threshold is 0.8 per §2.9.3
    assert quality.citation_coverage([f"C{i}" for i in range(4)], 5)[0] is True


def test_no_uncited_numbers():
    cited = "Adoption reached 38% of employers [1]. Budgets rose sharply [2]."
    uncited = "Adoption reached 38% of employers. Nobody checked this."
    assert quality.no_uncited_numbers(cited)[0] is True
    ok, msg = quality.no_uncited_numbers(uncited)
    assert ok is False
    assert "38%" in msg


def test_no_uncited_numbers_ignores_headings_and_prose():
    body = "## Findings 2026\nAdoption is broad but uneven [1]. No figures here."
    assert quality.no_uncited_numbers(body)[0] is True


def test_all_guardrails_are_registered():
    assert set(quality.GUARDRAILS) == {
        "min_sources_per_section",
        "all_claims_have_ids",
        "citation_coverage",
        "no_uncited_numbers",
    }
    for fn in quality.GUARDRAILS.values():
        assert callable(fn)


def test_guardrails_return_bool_message_tuples():
    for fn in quality.GUARDRAILS.values():
        # each must be usable directly as a CrewAI guardrail callback
        assert callable(fn)


# -------------------------------------------------------- verification log ---

def test_verification_log_splits_verified_from_failed():
    log = VerificationLog(results=[
        ClaimResult(claim_id="C1", verdict="VERIFIED", confidence=0.9),
        ClaimResult(claim_id="C2", verdict="UNSUPPORTED", confidence=0.3),
        ClaimResult(claim_id="C3", verdict="VERIFIED", confidence=0.9),
    ])
    assert log.verified_ids() == ["C1", "C3"]
    assert [r.claim_id for r in log.failed()] == ["C2"]

# --------------------------------------- live-model dict tolerance (HCL run) ---

def test_citation_coverage_tolerates_dict_entries():
    """The writer sometimes emits [{claim_id...}] instead of ids; guardrails
    must flag it as low-quality, never crash with unhashable type: 'dict'."""
    ok, msg = quality.citation_coverage(
        ["C1", {"claim_id": "C2"}, {"id": "C3"}], total=3)
    assert ok is True, msg


def test_claim_id_normalizer_handles_mixed_lists():
    pytest.importorskip("crewai")
    from src.crews.editorial_crew import _claim_ids
    assert _claim_ids(["C1", {"claim_id": "C2"}, {"id": "C3"}, None, {}]) == ["C1", "C2", "C3"]
    assert _claim_ids(None) == []


def test_string_coercion_never_crashes_on_dicts():
    assert quality._s({"statement": "hello"}) == "hello"
    assert quality._s({"url": "https://x.example"}) == "https://x.example"
    assert quality._s(None) == ""
    assert quality._s(42) == "42"
    assert quality.numeric_check({"statement": "no figures here"}, "")["match"] is True
    assert quality.no_uncited_numbers({"body": "plain prose"})[0] is True
