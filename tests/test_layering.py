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

ENGINE = ["risk", "stress", "questionnaire", "compare", "holdings", "synthetic", "charts",
          "profile", "profile_config", "books"] + [
          # the small-business engine (Phase 2 port of the FIS skeleton)
          "rand", "jsmath", "business_config", "company", "facts", "sales", "model", "levers", "cash",
          "measures", "tax", "brain_check"]
# Modules that must also be pure: no I/O, no clock, no environment, no unseeded randomness.
PURE = ["rand", "jsmath", "business_config", "company", "facts", "sales", "model", "levers", "cash",
        "measures", "tax", "brain_check", "books"]
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


FORBIDDEN_IMPORTS = {"os", "sys", "io", "time", "random", "pathlib", "socket", "subprocess", "shutil",
                     "tempfile", "httpx", "requests", "urllib", "psycopg", "logging"}
FORBIDDEN_CALLS = {"open", "now", "today", "utcnow", "time", "getenv", "input", "print"}


@pytest.mark.parametrize("name", PURE)
def test_small_business_engine_is_pure(name):
    """No I/O, no network, no clock, no os.environ, no unseeded randomness: the same
    snapshot and brain always give the same figures."""
    path = PKG / f"{name}.py"
    tree = ast.parse(path.read_text())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_IMPORTS]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            if node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                bad.append(node.module)
        elif isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if fname in FORBIDDEN_CALLS:
                bad.append(f"{fname}() at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr == "environ":
            bad.append("environ")
    assert bad == [], f"{name}: {bad}"
