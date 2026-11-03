"""Load a local `.env` file into the environment, once per process.

Configuration (API keys, market data vendor, Supabase) is read from environment
variables. On a host such as Render those are set in the dashboard; locally they can
live in a `.env` file (git-ignored; copy `.env.example`). Variables already set in the
real environment win over the file, so a host's settings are never overridden.

Looks for `.env` in the current directory, then at the repository root. Set
ADVISOR_NO_DOTENV=1 to skip the file entirely (the test suite does, so a developer's
own keys never leak into tests).
"""

from __future__ import annotations

import os
from pathlib import Path

from .paths import REPO_ROOT

_loaded = False


def load_env() -> None:
    global _loaded
    if _loaded or os.environ.get("ADVISOR_NO_DOTENV"):
        return
    _loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv not installed: use the real environment only
        return
    for path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
