"""Simple REPL: `python -m advisor_engine.assistant [--holdings PATH]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..paths import DEFAULT_HOLDINGS
from .chat import llm_mode, run_turn
from .tools import resolve_holdings_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m advisor_engine.assistant")
    ap.add_argument("--holdings", default=str(DEFAULT_HOLDINGS), help="holdings CSV inside data/")
    args = ap.parse_args(argv)

    try:
        holdings = str(resolve_holdings_path(str(Path(args.holdings).resolve())))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        mode = llm_mode()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    label = "MOCK (offline, no API calls)" if mode == "mock" else "LIVE (Anthropic API)"
    print(f"Mode: {label}")
    print(f"Holdings: {holdings}\nAsk about the portfolio's risk. /quit to exit.")
    history: list = []
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in ("/quit", "/exit"):
            return 0
        try:
            result = run_turn(q, history, default_holdings=holdings)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        history = result.history
        print(f"\n{result.reply}")


if __name__ == "__main__":
    sys.exit(main())
