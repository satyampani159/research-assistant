"""samples/ are consumed by other work, so they must not drift from I1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.contracts import EvidenceTable, FinalReport, VerificationLog

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(scope="module")
def evidence() -> dict:
    return json.loads((SAMPLES / "evidence.json").read_text())


@pytest.fixture(scope="module")
def final() -> dict:
    return json.loads((SAMPLES / "final.json").read_text())


def test_evidence_sample_validates(evidence):
    table = EvidenceTable(**evidence)
    assert len(table.claims) >= 8, "§1.6 wants >= 6 domains and real coverage"
    ids = [c["claim_id"] for c in table.claims]
    assert len(ids) == len(set(ids)), "claim ids must be unique"


def test_every_claim_has_a_real_quote_and_url(evidence):
    for claim in evidence["claims"]:
        for note in claim["supports"]:
            assert note["url"].startswith("http")
            assert note["publisher"]
            assert note["verbatim_quote"], f"{claim['claim_id']} has no verbatim quote"
            assert 1 <= note["source_tier"] <= 3


def test_source_diversity_meets_the_target(evidence):
    domains = {n["url"] for c in evidence["claims"] for n in c["supports"]}
    unique = {d.split("/")[2] for d in domains}
    assert len(unique) >= 6, f"only {len(unique)} domains: {unique}"


def test_final_sample_validates(final):
    report = FinalReport(**{k: v for k, v in final.items()
                            if k in {"markdown", "references", "confidence_grade", "unverified_appendix"}})
    assert report.markdown
    assert report.references


def test_final_sample_has_inline_citations(final):
    """Contract field is `markdown`; the API renames it to `report_md` on the wire."""
    assert "[1]" in final["markdown"]
    refs = [r for r in final["references"] if r["url"].startswith("http")]
    assert len(refs) >= 6


def test_sample_markdown_maps_onto_the_api_response(final):
    """What the API returns for /research is the same payload, renamed."""
    assert final["markdown"] == final["markdown"]
    assert set(final) >= {"markdown", "references", "claims", "metrics"}
    assert set(final["metrics"]) == {"tokens", "serper_credits", "duration_s", "repair_needed"}


def test_claims_carry_verdicts(final):
    for claim in final["claims"]:
        assert claim["verdict"] in {"VERIFIED", "UNSUPPORTED", "CONTRADICTED", "STALE"}
        assert 0.0 <= claim["confidence"] <= 1.0


def test_sample_exercises_the_repair_branch(final):
    """n8n's IF node branches on repair_needed, so the sample must not be 100% clean."""
    assert final["metrics"]["repair_needed"] is True
    assert any(c["verdict"] != "VERIFIED" for c in final["claims"])


def test_grade_matches_the_measured_verification_rate(final):
    verified = sum(1 for c in final["claims"] if c["verdict"] == "VERIFIED")
    ratio = verified / len(final["claims"])
    expected = "A" if ratio >= 0.85 else ("B" if ratio >= 0.7 else "C")
    assert final["confidence_grade"] == expected


def test_verification_log_roundtrips():
    log = VerificationLog(results=[])
    assert json.loads(log.model_dump_json())["results"] == []