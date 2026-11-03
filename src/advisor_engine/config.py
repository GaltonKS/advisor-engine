"""Process configuration read from the environment (the host, or a local `.env`).

Settings that more than one part of the package needs live here, so the API never
has to import the assistant to learn, say, which model client to use.
"""

from __future__ import annotations

import os

from ._env import load_env


def llm_mode() -> str:
    """"mock" or "anthropic". ADVISOR_LLM wins; otherwise live only if an API key is set."""
    load_env()
    mode = os.environ.get("ADVISOR_LLM", "").strip().lower()
    if mode in ("mock", "anthropic"):
        return mode
    if mode:
        raise ValueError(f"ADVISOR_LLM must be 'mock' or 'anthropic', not {mode!r}.")
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "mock"
