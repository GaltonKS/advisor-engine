"""Filesystem locations shared across the package (engine, data layer, assistant, API)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DEFAULT_HOLDINGS = DATA_DIR / "sample" / "sample_holdings.csv"
AUDIT_PATH = DATA_DIR / "audit" / "audit.jsonl"
