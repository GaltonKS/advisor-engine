"""Every environment variable the package reads is documented, in README.md's
"Environment variables" section and in .env.example, and read only in the config
modules."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "advisor_engine"
CONFIG_MODULES = {SRC / "config.py", SRC / "api" / "settings.py", SRC / "_env.py"}


def env_reads() -> dict[Path, set[str]]:
    out = {}
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        names = set(re.findall(r"os\.environ(?:\.get)?\(?\[?\s*[\"']([A-Z0-9_]+)[\"']", text))
        names |= set(re.findall(r"os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']", text))
        if "os.environ" in text or "os.getenv" in text:
            out[path] = names
    return out


def all_names() -> set[str]:
    from advisor_engine import config

    return set().union(*env_reads().values()) | set(config.KEY_VARS.values())


def test_environment_is_read_only_in_config_modules():
    assert set(env_reads()) <= CONFIG_MODULES, set(env_reads()) - CONFIG_MODULES


def test_every_variable_is_in_the_readme_table():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("## Environment variables", 1)[1].split("\n## ", 1)[0]
    missing = sorted(n for n in all_names() if f"`{n}`" not in section)
    assert not missing, missing


def test_every_variable_is_in_env_example():
    example = (ROOT / ".env.example").read_text()
    missing = sorted(n for n in all_names() if not re.search(rf"\b{n}\b", example))
    assert not missing, missing
