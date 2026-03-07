from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from src.config import load_settings


def reset_state_file(path: Path) -> None:
    path.write_text(
        json.dumps({"seen_links": [], "pending": {}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset bot state file for clean testing.")
    parser.add_argument(
        "--state-file",
        dest="state_file",
        default="",
        help="Optional path to state file. If omitted, value from .env STATE_FILE is used.",
    )
    args = parser.parse_args()

    load_dotenv()
    state_file = args.state_file.strip()
    if not state_file:
        state_file = load_settings().state_file

    path = Path(state_file)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    reset_state_file(path)
    print(f"State reset: {path}")


if __name__ == "__main__":
    main()
