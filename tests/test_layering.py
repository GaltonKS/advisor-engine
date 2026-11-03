"""The package's dependency direction, checked by parsing imports.

    engine modules  <-  analysis / paths / config / data  <-  assistant, api

The engine (risk math, stress, questionnaire, compare, holdings, synthetic data,
charts) must never import the LLM layer or the API, and neither may the shared layer
in between. The API may use the assistant (run_turn, verify, the tools), not the
other way round.
"""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "advisor_engine"

ENGINE = ["risk", "stress", "questionnaire", "compare", "holdings", "synthetic", "charts"]
SHARED = ["analysis", "paths", "config", "_env", "data"]


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    """Absolute names of every advisor_engine module `path` imports (at any depth)."""
    tree = ast.parse(path.read_text())
    here = _module_name(path).split(".")
    package = here if path.name == "__init__.py" else here[:-1]
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}  # `from . import x`
    return {m for m in out if m == "advisor_engine" or m.startswith("advisor_engine.")}


def files_of(name: str) -> list[Path]:
    d = PKG / name
    return sorted(d.rglob("*.py")) if d.is_dir() else [PKG / f"{name}.py"]


def _forbidden(path: Path, layers: tuple[str, ...]) -> list[str]:
    return sorted(m for m in imports_of(path)
                  if any(m == f"advisor_engine.{x}" or m.startswith(f"advisor_engine.{x}.")
                         for x in layers))


@pytest.mark.parametrize("name", ENGINE)
def test_engine_never_imports_assistant_api_or_shared_layer(name):
    for path in files_of(name):
        assert path.is_file(), path
        assert not _forbidden(path, ("assistant", "api", "analysis", "config", "data")), path


@pytest.mark.parametrize("name", SHARED)
def test_shared_layer_never_imports_assistant_or_api(name):
    for path in files_of(name):
        assert path.is_file(), path
        assert not _forbidden(path, ("assistant", "api")), path


def test_assistant_never_imports_api():
    for path in files_of("assistant"):
        assert not _forbidden(path, ("api",)), path


def test_import_parser_resolves_relative_imports():
    # Guards the guard: a relative import must resolve to its absolute name.
    found = imports_of(PKG / "api" / "routers" / "assistant.py")
    assert "advisor_engine.assistant.chat" in found
    assert "advisor_engine.api.deps" in found
