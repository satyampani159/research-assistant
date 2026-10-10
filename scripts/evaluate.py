"""Live evaluation: run a set of topics through POST /research and report real numbers.

Replaces the "not yet measured" rows in the README. Needs the stack running
(docker compose up) with real keys; each topic costs search credits and tokens.

    python scripts/evaluate.py                       # built-in topics
    python scripts/evaluate.py --topics-file t.txt   # one topic per line
    python scripts/evaluate.py --limit 3 --depth brief
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

DEFAULT_TOPICS = [
    "Recent advances in retrieval-augmented generation",
    "State of AI coding assistants adoption in 2026",
    "How agent frameworks handle tool-call reliability",
    "Enterprise adoption of open-weight language models",
    "Evaluation methods for LLM-based research agents",
]


def run_one(api: str, topic: str, depth: str, timeout: int, out_dir: Path | None = None) -> dict:
    started = time.monotonic()
    row = {"topic": topic, "ok": False}
    try:
        r = requests.post(f"{api}/research", json={"topic": topic, "depth": depth}, timeout=timeout)
        if r.status_code != 200:
            row["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
            return row
        body = r.json()
    except requests.RequestException as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    if out_dir is not None:  # keep the report itself so it can be read, not just scored
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "-" for c in topic.lower())[:40].strip("-")
        (out_dir / f"{slug}.md").write_text(body.get("report_md", ""), encoding="utf-8")
        (out_dir / f"{slug}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    claims = body.get("claims", [])
    refs = body.get("references", [])
    cited = [x for x in refs if x.get("role") != "further_reading"]
    domains = {urlparse(x.get("url", "")).netloc.removeprefix("www.") for x in cited} - {""}
    verified = sum(1 for c in claims if c.get("verdict") == "VERIFIED")
    m = body.get("metrics", {})
    row.update(
        ok=True,
        status=body.get("status", "complete"),
        claims=len(claims),
        verified=verified,
        verification_rate=round(verified / len(claims), 2) if claims else 0.0,
        cited_domains=len(domains),
        references=len(cited),
        serper_credits=m.get("serper_credits", 0),
        tokens=m.get("tokens", 0),
        duration_s=round(time.monotonic() - started, 1),
        repair_needed=m.get("repair_needed", False),
    )
    return row


def summarize(rows: list[dict]) -> dict:
    done = [r for r in rows if r["ok"]]
    complete = [r for r in done if r["status"] == "complete"]

    def mean(key):
        vals = [r[key] for r in complete]
        return round(statistics.mean(vals), 2) if vals else None

    return {
        "topics": len(rows),
        "request_success_rate": round(len(done) / len(rows), 2) if rows else 0,
        "complete_rate": round(len(complete) / len(rows), 2) if rows else 0,
        "degraded_rate": round((len(done) - len(complete)) / len(rows), 2) if rows else 0,
        "verification_rate_mean": mean("verification_rate"),
        "cited_domains_mean": mean("cited_domains"),
        "serper_credits_mean": mean("serper_credits"),
        "tokens_mean": mean("tokens"),
        "duration_s_mean": mean("duration_s"),
        "repair_trigger_rate": round(sum(r["repair_needed"] for r in complete) / len(complete), 2) if complete else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8010")
    ap.add_argument("--depth", default="standard", choices=["brief", "standard", "deep"])
    ap.add_argument("--topics-file")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N topics")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default="outputs")
    args = ap.parse_args()

    topics = DEFAULT_TOPICS
    if args.topics_file:
        topics = [t.strip() for t in Path(args.topics_file).read_text(encoding="utf-8").splitlines() if t.strip()]
    if args.limit:
        topics = topics[: args.limit]

    rows = []
    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] {topic}", flush=True)
        row = run_one(args.api, topic, args.depth, args.timeout, Path(args.out) / "eval-reports")
        print("   ", json.dumps({k: v for k, v in row.items() if k != "topic"}), flush=True)
        rows.append(row)

    summary = summarize(rows)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"eval-{stamp}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")

    print("\n== summary ==")
    for k, v in summary.items():
        print(f"{k:26} {v}")
    print(f"\nsaved {path}")
    return 0 if summary["request_success_rate"] else 1


if __name__ == "__main__":
    sys.exit(main())
