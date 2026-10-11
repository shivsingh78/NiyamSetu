"""Explicitly download the event-provided Track 1 JSONL dataset from GitHub.

This is not a legal update check: it only fetches the hackathon dataset and does
not establish whether the legal text is current or authoritative.
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "bhumi_vikas_rules.jsonl"
URL = "https://raw.githubusercontent.com/Vishal4742/bhopal-impact-lab-2026-problem-statements/main/track-1-bhumi-vikas-sahayak/data/bhumi_vikas_rules.jsonl"
MAX_BYTES = 5 * 1024 * 1024


def main() -> int:
    request = urllib.request.Request(URL, headers={"User-Agent": "NiyamSetu-dataset-loader/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Dataset download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if len(content) > MAX_BYTES:
        print("Dataset exceeded the 5 MB safety limit; not saved.", file=sys.stderr)
        return 1
    text = content.decode("utf-8")
    records = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON on line {line_no}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(obj, dict) or not str(obj.get("rule", "")).strip() or not str(obj.get("text", "")).strip():
            print(f"Missing rule or text on line {line_no}; not saved.", file=sys.stderr)
            return 1
        records.append(obj)
    if not records:
        print("No records downloaded; not saved.", file=sys.stderr)
        return 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.exists():
        backup = DEST.with_name("bhumi_vikas_rules.previous.jsonl")
        if not backup.exists():
            backup.write_bytes(DEST.read_bytes())
    DEST.write_text(text, encoding="utf-8")
    print(f"Saved {len(records)} records to {DEST}")
    print("Important: this is the event-provided consolidated dataset, not a verification that the provisions are current law.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
