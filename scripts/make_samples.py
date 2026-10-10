#!/usr/bin/env python3
"""D1.7 — build samples/evidence.json and samples/final.json.

These are the fixtures other work consumes: the API's offline engine double,
the n8n demo payload, and the shape reference for contract conformance.

They are built deterministically from fixtures/ — no LLM call, no keys — so
they are a *shape* reference, not a record of a real crew run. Replace them by
hand after a live run if you want a genuine transcript.

    python scripts/make_samples.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import quality, tools  # noqa: E402
from src.contracts import EvidenceTable, FinalReport  # noqa: E402

NOW = "2026-03-11T09:00:00+00:00"

TOPIC = "State of AI coding assistants, 2026"

SUB_QUESTIONS = [
    ("SQ1", "How widely are AI coding assistants adopted, and by whom?"),
    ("SQ2", "How well do coding agents actually perform on real work?"),
    ("SQ3", "What does it cost, and what constrains enterprise rollout?"),
]

# (claim_id, sub_question_id, statement, url) — statements must be quotable
# from the fixture page so verify_claim can reproduce them.
CLAIMS = [
    ("C1", "SQ1",
     "84% of professional developers are using or planning to use AI coding tools",
     "https://stackoverflow.blog/2025/07/developer-survey-2025-ai-results/"),
    ("C2", "SQ1",
     "Only 29% of professional developers trust the accuracy of AI tooling for complex tasks",
     "https://stackoverflow.blog/2025/07/developer-survey-2025-ai-results/"),
    ("C3", "SQ1",
     "Developers accepted just over 30% of Copilot's code suggestions",
     "https://github.blog/news-insights/research/research-quantifying-github-copilots-impact-on-developer-productivity-and-happiness/"),
    ("C4", "SQ2",
     "The strongest system completed 41% of tasks end-to-end without human intervention",
     "https://arxiv.org/abs/2603.01234"),
    ("C5", "SQ2",
     "Task completion drops sharply for tasks requiring more than three file edits",
     "https://arxiv.org/abs/2603.01234"),
    ("C6", "SQ3",
     "Adoption of AI coding agents reached 38% this year, up from 21% a year earlier",
     "https://www.reuters.com/technology/enterprise-adoption-coding-agents-2026-03-11/"),
    ("C7", "SQ3",
     "Annual spend on coding agents grew to $1.2B",
     "https://techcrunch.com/2026/05/12/coding-agents-enterprise-pricing/"),
    ("C8", "SQ3",
     "High-risk AI system obligations apply from 2 August 2026",
     "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai"),
    # Two deliberately bad claims, so the sample exercises the repair path that
    # n8n's IF node branches on (metrics.repair_needed must be true here).
    ("C9", "SQ2",
     "The strongest system completed 91% of tasks end-to-end without human intervention",
     "https://arxiv.org/abs/2603.01234"),
    ("C10", "SQ3",
     "Vendors charge $19 per seat per month",
     "https://techcrunch.com/2026/05/12/coding-agents-enterprise-pricing/"),
]


def note_for(claim_id: str, sub_question_id: str, statement: str, url: str) -> dict:
    page = tools.fetch_page(url)
    return {
        "claim_id": claim_id,
        "sub_question_id": sub_question_id,
        "statement": statement,
        "url": url,
        "publisher": tools.domain_of(url),
        "date": None,
        "retrieved_at": NOW,
        "verbatim_quote": quality.best_quote(statement, page["text"]),
        "source_tier": tools.tier_of(url),
    }


def build_evidence() -> dict:
    claims = [
        {
            "claim_id": cid,
            "statement": statement,
            "supports": [note_for(cid, sq, statement, url)],
            "conflicts": [],
        }
        for cid, sq, statement, url in CLAIMS
    ]
    table = EvidenceTable(
        claims=claims,
        gaps=["No independent measurement of long-horizon maintenance cost."],
        contradictions=[
            "Reported trust (29%) is far below reported usage (84%); usage does not "
            "imply verification."
        ],
    )
    data = table.model_dump()
    data.update(
        topic=TOPIC,
        depth="standard",
        serper_credits=0,
        generated_from="fixtures (deterministic, no LLM)",
    )
    return data


def build_final(evidence: dict) -> dict:
    results = []
    for claim in evidence["claims"]:
        note = claim["supports"][0]
        verdict = quality.verify_claim(claim["claim_id"], claim["statement"], note["url"])
        results.append(
            {
                "claim_id": verdict["claim_id"],
                "verdict": verdict["verdict"],
                "confidence": verdict["confidence"],
                "evidence_quote": verdict["evidence_quote"],
                "checked_url": verdict["checked_url"],
                "checked_at": verdict["checked_at"],
            }
        )

    refs, lines = [], []
    for idx, (cid, *_rest) in enumerate(CLAIMS, start=1):
        note = evidence["claims"][idx - 1]["supports"][0]
        refs.append(
            {
                "url": note["url"],
                "title": note["statement"][:80],
                "publisher": note["publisher"],
                "date": note["date"],
            }
        )
        lines.append(f"- {note['statement']} [{idx}]")

    unverified = [
        f"{r['claim_id']}: {r['evidence_quote'] or 'no supporting quote'} ({r['verdict']})"
        for r in results
        if r["verdict"] != "VERIFIED"
    ]
    verified = sum(1 for r in results if r["verdict"] == "VERIFIED")
    grade = "A" if verified / len(results) >= 0.85 else ("B" if verified / len(results) >= 0.7 else "C")

    markdown = "\n".join(
        [
            f"# {TOPIC}",
            "",
            "## Adoption is broad; trust is not",
            "",
            *lines,
            "",
            "## Verification summary",
            "",
            f"- claims checked: {len(results)}",
            f"- verified: {verified}",
            f"- not verified: {len(results) - verified}",
            f"- unique source domains: {len({r['url'] for r in refs})}",
            "",
            "## Methods",
            "",
            "Every claim above was re-fetched from its cited URL and checked by "
            "deterministic numeric comparison before any model judgement.",
            "",
        ]
    )

    final = FinalReport(
        markdown=markdown,
        references=refs,
        confidence_grade=grade,
        unverified_appendix=unverified,
    )
    data = final.model_dump()
    data["claims"] = [
        {
            "id": r["claim_id"],
            "statement": evidence["claims"][i]["statement"],
            "verdict": r["verdict"],
            "confidence": r["confidence"],
        }
        for i, r in enumerate(results)
    ]
    data["metrics"] = {
        "tokens": 0,
        "serper_credits": 0,
        "duration_s": 0.0,
        "repair_needed": any(r["verdict"] != "VERIFIED" for r in results),
    }
    data["generated_from"] = "fixtures (deterministic, no LLM)"
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def main() -> int:
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    evidence = build_evidence()
    final = build_final(evidence)
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (out / "final.json").write_text(json.dumps(final, indent=2) + "\n")

    verdicts = [c["verdict"] for c in final["claims"]]
    domains = {r["url"] for r in final["references"]}
    print(f"samples/evidence.json  {len(evidence['claims'])} claims")
    print(f"samples/final.json     grade {final['confidence_grade']}")
    print(f"  verdicts: {verdicts}")
    print(f"  unique domains: {len(domains)}")
    print(f"  unverified: {len(final['unverified_appendix'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())