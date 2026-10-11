"""Build-time check: fail the deploy if the full dataset is missing or the wrong size."""
from __future__ import annotations
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from niyamsetu import FULL_DATA, _load_jsonl  # noqa: E402


def main() -> int:
    expected = int(os.environ.get("NIYAMSETU_EXPECTED_RECORDS", "106"))
    if not FULL_DATA.exists():
        print(f"Full dataset missing: data/{FULL_DATA.name}. Commit it or run scripts/download_dataset.py.", file=sys.stderr)
        return 1
    count = len(_load_jsonl(FULL_DATA))
    if count != expected:
        print(f"Dataset has {count} records, expected {expected}.", file=sys.stderr)
        return 1
    print(f"Dataset OK: data/{FULL_DATA.name} ({count} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
