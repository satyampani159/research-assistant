#!/usr/bin/env python3
"""Import and activate n8n workflow via REST API.

Usage:
    python scripts/import_n8n.py                    # auto-detect n8n URL
    python scripts/import_n8n.py --n8n-url http://localhost:5678
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests


def wait_n8n(base_url: str, timeout: int = 60) -> bool:
    """Wait for n8n to be ready."""
    for _ in range(timeout):
        try:
            if requests.get(f"{base_url}/healthz", timeout=2).ok:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def import_workflow(base_url: str, workflow_path: Path) -> str:
    """Import and activate workflow via n8n REST API."""
    with open(workflow_path) as f:
        workflow = json.load(f)

    # Import workflow
    r = requests.post(f"{base_url}/rest/workflows", json=workflow, timeout=15)
    r.raise_for_status()
    workflow_id = r.json()["id"]

    # Activate workflow
    requests.post(f"{base_url}/rest/workflows/{workflow_id}/activate", timeout=10).raise_for_status()

    return workflow_id


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Import n8n workflow")
    ap.add_argument("--n8n-url", default="http://localhost:5678", help="n8n base URL")
    ap.add_argument("--workflow", default="n8n/research-assistant.json", help="Workflow JSON path")
    args = ap.parse_args()

    workflow_path = Path(args.workflow)
    if not workflow_path.exists():
        print(f"Workflow not found: {workflow_path}", file=sys.stderr)
        return 1

    print(f"Waiting for n8n at {args.n8n_url}...")
    if not wait_n8n(args.n8n_url):
        print("n8n not ready after timeout", file=sys.stderr)
        return 1

    print("n8n ready. Importing workflow...")
    try:
        wf_id = import_workflow(args.n8n_url, workflow_path)
        print(f"Successfully imported and activated workflow: {wf_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())