#!/usr/bin/env python3
"""One-command demo: health → import n8n → webhook → PDF report.

Usage:
    python scripts/demo.py --topic "State of AI coding assistants, 2026" --depth brief
    python scripts/demo.py --topic "Recent advances in RAG" --depth standard --api-url http://localhost:8010
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from fpdf import FPDF


def get_default_urls() -> tuple[str, str]:
    """Get default API and n8n URLs based on environment."""
    # If running inside Docker container, use service names
    if os.path.exists("/.dockerenv") or os.getenv("IN_DOCKER"):
        return "http://crew-api:8000", "http://n8n:5678"
    # Otherwise use localhost with default ports
    return "http://localhost:8010", "http://localhost:5679"


class PDFReport(FPDF):
    """Simple PDF report generator."""

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)
        font_dir = "/usr/share/fonts/truetype/dejavu"
        self.add_font("DejaVu", "", f"{font_dir}/DejaVuSans.ttf")
        self.add_font("DejaVu", "B", f"{font_dir}/DejaVuSans-Bold.ttf")
        self.add_font("DejaVu", "I", f"{font_dir}/DejaVuSans-Oblique.ttf")
        self.add_font("DejaVu", "BI", f"{font_dir}/DejaVuSans-BoldOblique.ttf")

    def header(self):
        if self.page_no() > 1:
            self.set_font("DejaVu", "I", 8)
            self.cell(0, 5, "Research Assistant Report", align="C")
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("DejaVu", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def add_title(self, title: str, topic: str, audience: str, depth: str):
        self.set_font("DejaVu", "B", 18)
        self.cell(0, 12, title, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 11)
        self.cell(0, 7, f"Topic: {topic}", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 7, f"Audience: {audience} | Depth: {depth}", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self.set_draw_color(100, 100, 100)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(5)

    def add_section(self, heading: str, body: str):
        self.set_font("DejaVu", "B", 13)
        self.cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 10)
        self.multi_cell(0, 5, body)
        self.ln(3)

    def add_metrics(self, metrics: dict):
        self.set_font("DejaVu", "B", 12)
        self.cell(0, 8, "Metrics", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 10)
        for k, v in metrics.items():
            self.cell(0, 6, f"  {k}: {v}", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def add_references(self, refs: list[dict]):
        if not refs:
            return
        self.set_font("DejaVu", "B", 12)
        self.cell(0, 8, "References", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 9)
        for i, r in enumerate(refs, 1):
            title = r.get("title", "")[:80]
            url = r.get("url", "")
            pub = r.get("publisher", "")
            date = r.get("date", "")
            line = f"[{i}] {title}"
            if pub:
                line += f" — {pub}"
            if date:
                line += f" ({date})"
            self.multi_cell(0, 5, line)
            self.set_font("DejaVu", "I", 8)
            self.cell(0, 4, f"    {url}", new_x="LMARGIN", new_y="NEXT")
            self.set_font("DejaVu", "", 9)
            self.ln(2)


def markdown_to_pdf_sections(markdown: str) -> list[tuple[str, str]]:
    """Convert markdown to (heading, body) sections for PDF."""
    sections = []
    current_heading = "Introduction"
    current_body = []

    for line in markdown.split("\n"):
        line = line.rstrip()
        if line.startswith("# "):
            if current_body:
                sections.append((current_heading, "\n".join(current_body).strip()))
            current_heading = line[2:].strip()
            current_body = []
        elif line.startswith("## "):
            if current_body:
                sections.append((current_heading, "\n".join(current_body).strip()))
            current_heading = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_heading, "\n".join(current_body).strip()))

    return sections


def generate_pdf(report_data: dict, output_path: Path) -> Path:
    """Generate PDF from report data."""
    if FPDF is None:
        raise RuntimeError("fpdf2 not installed. Run: pip install fpdf2")

    pdf = PDFReport()
    pdf.alias_nb_pages()
    pdf.add_page()

    # Title page
    pdf.add_title(
        "Research Report",
        report_data.get("topic", "Research Topic"),
        report_data.get("audience", "general professional"),
        report_data.get("depth", "brief"),
    )

    # Parse markdown into sections
    markdown = report_data.get("report_md", "")
    sections = markdown_to_pdf_sections(markdown)

    for heading, body in sections:
        if body:
            pdf.add_section(heading, body)

    # Metrics
    metrics = report_data.get("metrics", {})
    if metrics:
        pdf.add_metrics(metrics)

    # References
    refs = report_data.get("references", [])
    if refs:
        pdf.add_references(refs)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    return output_path


def wait_for_health(api_url: str, timeout: int = 60) -> dict:
    """Wait for API health endpoint."""
    for _ in range(timeout):
        try:
            r = requests.get(f"{api_url}/health", timeout=3)
            if r.ok:
                return r.json()
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"API at {api_url} not healthy after {timeout}s")


def import_n8n_workflow(n8n_url: str, workflow_path: Path) -> str:
    """Import n8n workflow via REST API or CLI (best effort).

    Fast path: the compose entrypoint already imports + publishes on `up`,
    so if an active workflow exists we return immediately without touching
    the auth-gated REST API.
    """
    import json
    import subprocess

    with open(workflow_path) as f:
        workflow = json.load(f)

    # Wait for n8n
    for _ in range(60):
        try:
            if requests.get(f"{n8n_url}/healthz", timeout=2).ok:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise RuntimeError(f"n8n at {n8n_url} not ready after 60s")

    # Fast path: check for an already-active workflow via CLI (no auth needed)
    try:
        active = subprocess.run(
            ["docker", "exec", "research-n8n", "n8n", "list:workflow", "--active=true"],
            capture_output=True, text=True, timeout=20,
        )
        if active.returncode == 0 and "Research Assistant" in active.stdout:
            wf_line = [l for l in active.stdout.splitlines() if "Research Assistant" in l]
            print(f"    Workflow already active: {wf_line[0].strip() if wf_line else 'ok'}")
            return wf_line[0].split("|")[0].strip() if wf_line else "already-active"
    except Exception as e:
        print(f"    Active-workflow check failed: {e}")

    # Try REST API (requires n8n auth / API key on recent n8n)
    try:
        r = requests.post(f"{n8n_url}/rest/workflows", json=workflow, timeout=15)
        if r.status_code == 401:
            print("    n8n REST API requires auth (401), trying CLI import...")
        else:
            r.raise_for_status()
            wf_id = r.json()["id"]
            requests.post(f"{n8n_url}/rest/workflows/{wf_id}/activate", timeout=10).raise_for_status()
            return wf_id
    except requests.exceptions.HTTPError as e:
        if getattr(e.response, "status_code", None) != 401:
            raise

    # Fallback: CLI import. Compose mounts ./n8n/research-assistant.json -> /workflow.json
    try:
        print("    Attempting n8n CLI import via docker exec...")
        result = subprocess.run([
            "docker", "exec", "research-n8n", "n8n", "import:workflow", "--input=/workflow.json",
        ], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # Re-list to get the (new) id, then publish it
            listed = subprocess.run(
                ["docker", "exec", "research-n8n", "n8n", "list:workflow"],
                capture_output=True, text=True, timeout=20,
            )
            wf_id = ""
            for line in listed.stdout.splitlines():
                if "Research Assistant" in line and "|" in line:
                    wf_id = line.split("|")[0].strip()
                    break
            if wf_id:
                pub = subprocess.run(
                    ["docker", "exec", "research-n8n", "n8n", "publish:workflow", f"--id={wf_id}"],
                    capture_output=True, text=True, timeout=20,
                )
                if pub.returncode == 0:
                    print(f"    Imported + published via CLI: {wf_id}")
                    return wf_id
            print("    Imported via n8n CLI")
            return "cli-imported"
        else:
            print(f"    CLI import failed: {result.stderr.strip() or result.stdout.strip()}")
    except Exception as e:
        print(f"    CLI import failed: {e}")

    print("    Could not import workflow automatically - please import manually in n8n UI")
    return "manual"


def fire_webhook(n8n_url: str, payload: dict, timeout: int = 600) -> dict:
    """Fire n8n webhook and wait for result.

    Raises a RuntimeError with the response body attached so 429 (engine
    busy) and 500 (e.g. ECONNRESET after a restart) are diagnosable instead
    of a bare HTTPError.
    """
    webhook_url = f"{n8n_url}/webhook/research-assistant"
    try:
        r = requests.post(webhook_url, json=payload, timeout=timeout)
    except requests.exceptions.ReadTimeout as exc:
        # Server keeps running after client timeout (n8n -> crew-api takes
        # minutes). Caller falls back to polling data/ for the report file.
        raise TimeoutError(
            f"webhook timed out after {timeout}s but the server-side run "
            f"continues; check data/report-*.md"
        ) from exc
    if r.status_code == 429:
        raise RuntimeError(f"engine busy (429): {r.text[:500]} — wait and retry once")
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"webhook failed {r.status_code}: {r.text[:2000]}"
        ) from exc
    return r.json()


def run_direct(api_url: str, payload: dict, timeout: int = 1800) -> dict:
    """Call crew-api /research directly (bypass n8n).

    Used when the crew run exceeds n8n's HTTP-node timeout: the API itself
    has no timeout, only this client does. Mirrors the /research contract.
    """
    url = f"{api_url}/research"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.ReadTimeout as exc:
        raise TimeoutError(
            f"direct /research timed out after {timeout}s but the server-side "
            f"run continues; poll /health busy flag and retry later"
        ) from exc
    if r.status_code == 429:
        raise RuntimeError(f"engine busy (429): {r.text[:500]} — wait and retry once")
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"direct /research failed {r.status_code}: {r.text[:2000]}"
        ) from exc
    return r.json()


def wait_for_idle(api_url: str, timeout: int = 900, interval: int = 15) -> bool:
    """Wait until /health reports busy=false (single-flight lock free)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{api_url}/health", timeout=5)
            if r.ok and not r.json().get("busy", False):
                return True
            print("    Engine busy, waiting for current run to finish...", flush=True)
        except Exception:
            pass
        time.sleep(interval)
    return False


def snapshot_reports(data_dir: Path) -> set[str]:
    """Names of existing report files, to detect newly written ones."""
    return {p.name for p in data_dir.glob("report-*")}


def wait_for_new_report(data_dir: Path, before: set[str], timeout: int = 600, interval: int = 10) -> Path | None:
    """Poll data/ for a report file that did not exist before the run."""
    start = time.time()
    while time.time() - start < timeout:
        current = sorted(data_dir.glob("report-*.md"))
        for p in current:
            if p.name not in before and p.stat().st_size > 0:
                return p
        time.sleep(interval)
    # Fall back to the latest report even if not new (better than nothing)
    latest = find_latest_report(data_dir)
    if latest and latest.name not in before:
        return latest
    return None


def find_latest_report(data_dir: Path) -> Path | None:
    """Find the latest report file in data directory."""
    reports = sorted(data_dir.glob("report-*.md"))
    if reports:
        return reports[-1]
    pdfs = sorted(data_dir.glob("report-*.pdf"))
    if pdfs:
        return pdfs[-1]
    return None


def main() -> int:
    default_api, default_n8n = get_default_urls()

    ap = argparse.ArgumentParser(description="Run full research demo and generate PDF")
    ap.add_argument("--topic", default="State of AI coding assistants, 2026")
    ap.add_argument("--audience", default="engineering managers")
    ap.add_argument("--depth", choices=["brief", "standard", "deep"], default="brief")
    ap.add_argument("--max-searches", type=int, default=15)
    ap.add_argument("--api-url", default=default_api)
    ap.add_argument("--n8n-url", default=default_n8n)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--output", default="", help="Custom output PDF path")
    ap.add_argument("--skip-import", action="store_true", help="Skip n8n workflow import (already active)")
    ap.add_argument("--no-wait", action="store_true", help="Do not wait for idle engine; fail fast if busy")
    ap.add_argument("--poll-timeout", type=int, default=600, help="Seconds to poll data/ for report after webhook timeout")
    ap.add_argument("--webhook-timeout", type=int, default=1500, help="Seconds to wait for n8n webhook (must be < n8n HTTP node timeout)")
    ap.add_argument("--direct", action="store_true", help="Bypass n8n: POST /research directly (no 600s n8n ceiling)")
    ap.add_argument("--direct-timeout", type=int, default=1800, help="Seconds to wait for direct /research (Ollama 8b can exceed 600s)")
    args = ap.parse_args()

    api_url = args.api_url.rstrip("/")
    n8n_url = args.n8n_url.rstrip("/")
    data_dir = Path(args.data_dir)

    print("=" * 60)
    print("RESEARCH ASSISTANT DEMO")
    print("=" * 60)
    print(f"Topic:      {args.topic}")
    print(f"Audience:   {args.audience}")
    print(f"Depth:      {args.depth}")
    print(f"Max searches: {args.max_searches}")
    print(f"API:        {api_url}")
    if args.direct:
        print(f"Mode:       DIRECT (bypass n8n, timeout {args.direct_timeout}s)")
    else:
        print(f"n8n:        {n8n_url}")
    print("-" * 60)

    # 1. Health check
    print("\n[1/5] Checking API health...")
    health = wait_for_health(api_url)
    mode = health.get("mode", "unknown")
    ollama_ok = health.get("providers", {}).get("ollama", {}).get("healthy", False)
    busy = health.get("busy", False)
    print(f"    Mode: {mode} | Ollama: {'OK' if ollama_ok else 'DOWN'} | Busy: {busy}")
    if busy and not args.no_wait:
        print("    Engine is busy with another report — waiting for it to finish...")
        if not wait_for_idle(api_url):
            print("    ERROR: engine still busy after wait. Retry later.", file=sys.stderr)
            return 1
        print("    Engine is now idle.")

    # 2. Try to import n8n workflow (best effort; skipped in direct mode)
    print("\n[2/5] Importing n8n workflow...")
    if args.direct:
        print("    Skipped (--direct: n8n bypassed)")
        wf_id = "direct"
    elif args.skip_import:
        print("    Skipped (--skip-import)")
        wf_id = "skipped"
    else:
        workflow_path = Path("n8n/research-assistant.json")
        wf_id = import_n8n_workflow(n8n_url, workflow_path)
        print(f"    Status: {wf_id}")

    # 3. Run research (n8n webhook OR direct API)
    if args.direct:
        print(f"\n[3/5] Running research via direct API (timeout {args.direct_timeout}s, may take 10-20 min on Ollama)...")
    else:
        print("\n[3/5] Running research via n8n webhook (this may take 1-3 minutes)...")
    payload = {
        "topic": args.topic,
        "audience": args.audience,
        "depth": args.depth,
        "max_searches": args.max_searches,
    }
    before = snapshot_reports(data_dir)
    result: dict = {}
    if args.direct:
        try:
            result = run_direct(api_url, payload, timeout=args.direct_timeout)
            print(f"    Direct /research completed")
        except (TimeoutError, RuntimeError) as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            return 1
        # Persist markdown like the n8n Write node does, so data/ holds the artifact
        md_text = result.get("report_md", "")
        if not md_text:
            print("    ERROR: API returned no report_md.", file=sys.stderr)
            return 1
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in args.topic)[:40]
        saved_md = data_dir / f"report-{safe_topic}-{timestamp}.md"
        saved_md.parent.mkdir(parents=True, exist_ok=True)
        saved_md.write_text(md_text)
        print(f"    Saved markdown: {saved_md}")
        # Persist full result JSON so the PDF can be regenerated without rerunning the crew
        saved_json = saved_md.with_suffix(".json")
        saved_json.write_text(json.dumps(
            {"topic": args.topic, "audience": args.audience, "depth": args.depth,
             "report_md": result.get("report_md", ""), "references": result.get("references", []),
             "claims": result.get("claims", []), "metrics": result.get("metrics", {})},
            ensure_ascii=False, indent=2))
        print(f"    Saved result JSON: {saved_json}")
    else:
        try:
            result = fire_webhook(n8n_url, payload, timeout=args.webhook_timeout)
            print(f"    Webhook completed")
        except TimeoutError as exc:
            # Client timed out but server keeps running: poll data/ for the file
            # the Write node produces (it runs before Respond to Webhook).
            print(f"    {exc}")
            print(f"    Polling {data_dir}/ for the report file ({args.poll_timeout}s)...")
            latest = wait_for_new_report(data_dir, before, timeout=args.poll_timeout)
            if not latest:
                print("    ERROR: timed out and no new report appeared.", file=sys.stderr)
                return 1
            print(f"    Found report file: {latest} (recovering from timeout)")
        except RuntimeError as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            return 1

    # 4. Find and process report
    print("\n[4/5] Processing report...")
    latest = find_latest_report(data_dir)
    if not latest:
        print("    ERROR: No report found in data directory", file=sys.stderr)
        return 1

    # Load markdown report
    if latest.suffix == ".md":
        markdown = latest.read_text()
    else:
        md_path = latest.with_suffix(".md")
        markdown = md_path.read_text() if md_path.exists() else ""

    # Combine API result with markdown
    report_data = {
        "topic": args.topic,
        "audience": args.audience,
        "depth": args.depth,
        "report_md": markdown or result.get("report_md", ""),
        "references": result.get("references", []),
        "metrics": result.get("metrics", {}),
    }

    # 5. Generate PDF
    print("\n[5/5] Generating PDF...")
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in args.topic)[:40]
        output_path = data_dir / f"report-{safe_topic}-{timestamp}.pdf"

    try:
        pdf_path = generate_pdf(report_data, output_path)
        print(f"    PDF saved: {pdf_path}")
    except Exception as exc:
        print(f"    PDF generation failed: {exc}", file=sys.stderr)
        md_path = output_path.with_suffix(".md")
        md_path.write_text(report_data.get("report_md", ""))
        print(f"    Saved markdown fallback: {md_path}")
        return 1

    # Summary
    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)
    print(f"Report:     {pdf_path}")
    print(f"Metrics:    {json.dumps(report_data.get('metrics', {}), indent=4)}")
    print(f"Mode:       {health.get('mode', 'unknown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())