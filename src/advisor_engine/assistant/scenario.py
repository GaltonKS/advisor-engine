"""Turn an adviser's described scenario into suggested stress-test shocks.

The AI suggests *assumptions* (an instant shock per sector and asset class, plus a
default), never results. The adviser reviews and edits them in the Stress test tab,
and the engine (stress.py) computes the portfolio's move. So the only AI-made numbers
an adviser sees are labelled inputs they approved; every result is engine output.

Safeguards, enforced here rather than trusted to the prompt:
- Structured output (`output_config.format`) limits the reply to the portfolio's own
  sectors and the shockable asset classes, as fractions.
- Shocks outside -100%..+100% are rejected; the engine checks again when it runs.
- The explanation (per-shock reasons and an overall rationale) is run through the
  number check against the suggested shocks only. If it quotes any other figure (an
  estimated loss, a probability, a date) it is withheld and the shocks are shown alone.

`suggest_shocks` takes a client: the Anthropic client in live mode, or `MockSuggester`
(keyword rules, no network) for tests, demos and dev auth.
"""

from __future__ import annotations

import json
import os
import re

import pandas as pd

from ..holdings import STANDARD_SECTORS, UNCLASSIFIED
from ..stress import SCENARIOS, SHOCKABLE_CLASSES
from .chat import DEFAULT_MODEL
from .numcheck import verify

MAX_TOKENS = 4096
WITHHELD = ("The AI's explanation quoted figures other than its suggested shocks, so it was "
            "withheld. Review the shocks themselves before running.")

SYSTEM = """\
You turn a financial adviser's description of a hypothetical scenario into stress-test \
assumptions for a portfolio risk engine. The engine applies an instant, one-off shock to \
each holding and computes the portfolio's move; you only propose the shocks.

Rules:
- Give shocks as fractions: -0.25 is a 25% fall, 0.05 a 5% rise. Use whole percentages \
(multiples of 0.01). Stay within -0.6 to 0.3 unless the scenario clearly calls for more.
- Shock only the sectors and asset classes listed; everything else takes the default \
shock. Leave a sector out if the scenario has no particular effect on it.
- Cash normally holds its value; only shock it if the scenario says so.
- Give each shock a one-sentence reason in plain language, and a short overall rationale.
- In reasons and the rationale, the only numbers you may use are the shocks you propose, \
written as percentages. Never estimate the portfolio's loss or value, probabilities, \
dates or index levels.
- You are proposing assumptions for the adviser to review, not predicting events. Say so \
plainly if the scenario is speculative.
- If the description is not a market scenario, return no sector or asset class shocks, \
a default of 0, and say why in the rationale.
"""


def portfolio_targets(holdings: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Sectors (excluding Unclassified) and shockable asset classes in the holdings."""
    sectors = sorted(s for s in holdings["sector"].unique() if s != UNCLASSIFIED)
    classes = sorted(c for c in holdings.get("asset_class", pd.Series(dtype=str)).unique()
                     if c in SHOCKABLE_CLASSES)
    return sectors, classes


def output_schema(sectors: list[str], classes: list[str]) -> dict:
    def shock_list(key: str, names: list[str]) -> dict:
        return {"type": "array", "items": {
            "type": "object",
            "properties": {key: {"type": "string", "enum": names},
                           "shock": {"type": "number"}, "reason": {"type": "string"}},
            "required": [key, "shock", "reason"], "additionalProperties": False}}

    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            # An enum can't be empty; with no classified sectors, offer the standard ones.
            "sector_shocks": shock_list("sector", sectors or list(STANDARD_SECTORS)),
            "asset_class_shocks": shock_list("asset_class", classes or list(SHOCKABLE_CLASSES)),
            "default_shock": {"type": "number"},
            "default_reason": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["label", "sector_shocks", "asset_class_shocks", "default_shock",
                     "default_reason", "rationale"],
        "additionalProperties": False,
    }


def _user_message(description: str, sectors: list[str], classes: list[str]) -> str:
    return (f"Scenario: {description.strip()}\n\n"
            f"Sectors in this portfolio: {', '.join(sectors) or 'none recorded'}\n"
            f"Asset classes that can be shocked: {', '.join(classes) or 'none held'}\n"
            "Holdings without a sector take the default shock.")


def _finish(raw: dict, description: str, sectors: list[str], classes: list[str],
            model: str, source: str) -> dict:
    """Validate a raw suggestion and check its explanation against its own shocks."""
    allowed_s = set(sectors) or set(STANDARD_SECTORS)
    allowed_c = set(classes) or set(SHOCKABLE_CLASSES)
    sector_shocks, class_shocks, reasons = {}, {}, []
    for key, allowed, out, kind in (("sector_shocks", allowed_s, sector_shocks, "sector"),
                                    ("asset_class_shocks", allowed_c, class_shocks, "asset_class")):
        for item in raw.get(key, []):
            name, shock = item[kind], round(float(item["shock"]), 4)
            if name not in allowed or name in out:
                continue  # off-list or repeated: the schema should prevent it; ignore if not
            if not -1.0 <= shock <= 1.0:
                raise ValueError("The AI suggested a shock outside -100% to +100%.")
            out[name] = shock
            reasons.append({"kind": kind, "target": name, "shock": shock,
                            "reason": str(item.get("reason", "")).strip()})
    default = round(float(raw.get("default_shock", 0.0)), 4)
    if not -1.0 <= default <= 1.0:
        raise ValueError("The AI suggested a shock outside -100% to +100%.")
    reasons.append({"kind": "default", "target": "All other holdings", "shock": default,
                    "reason": str(raw.get("default_reason", "")).strip()})

    rationale = str(raw.get("rationale", "")).strip()
    explanation = " ".join([rationale] + [r["reason"] for r in reasons])
    shocks_only = json.dumps({"shocks": [r["shock"] for r in reasons]})
    check = verify(explanation, [shocks_only])
    if not check["ok"]:
        rationale = WITHHELD
        for r in reasons:
            r["reason"] = ""
    return {
        "description": description.strip(),
        "label": str(raw.get("label", "")).strip()[:80] or "Custom scenario",
        "sector_shocks": sector_shocks,
        "asset_class_shocks": class_shocks,
        "default_shock": default,
        "reasons": reasons,
        "rationale": rationale,
        "explanation_withheld": not check["ok"],
        "unmatched": check["unmatched"],
        "model": model,
        "source": source,
    }


def suggest_shocks(description: str, holdings: pd.DataFrame, client=None,
                   model: str | None = None) -> dict:
    """Suggested shocks for `description` on these holdings (see module docstring).

    Raises ValueError for an unusable suggestion (refusal, out-of-range shock) and lets
    API errors propagate, so the caller can show a plain message.
    """
    if not description.strip():
        raise ValueError("Describe a scenario first.")
    sectors, classes = portfolio_targets(holdings)
    if isinstance(client, MockSuggester):
        return _finish(client.suggest(description, sectors, classes), description, sectors,
                       classes, "mock", "mock")

    model = model or os.environ.get("ADVISOR_MODEL", DEFAULT_MODEL)
    response = client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": _user_message(description, sectors, classes)}],
        output_config={"format": {"type": "json_schema", "schema": output_schema(sectors, classes)}},
        # If a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise ValueError("The AI declined to suggest shocks for this scenario.")
    if response.stop_reason == "max_tokens":
        raise ValueError("The AI's suggestion was cut off; try a shorter description.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _finish(json.loads(text), description, sectors, classes,
                   getattr(response, "model", model), "ai")


# --- offline stand-in ---------------------------------------------------------------------

def _pct(x: float) -> str:
    return "0%" if round(x * 100) == 0 else f"{x * 100:+.0f}%"


class MockSuggester:
    """Keyword rules shaped like an AI suggestion, for tests, demos and dev auth.

    Picks a preset shape from words in the description (rates, recession, chips/tech,
    oil, crash/war), keeps only the portfolio's sectors, and writes reasons that quote
    only its own shocks.
    """

    model_name = "mock"

    RULES = (
        (r"\b(rate|rates|interest|fed|hike|inflation|yield)", "rising_rates"),
        (r"\b(recession|unemployment|downturn|layoffs)", "recession"),
        (r"\b(chip|chips|semiconductor|taiwan|tech|ai bubble)", "tech_selloff"),
        (r"\b(crash|panic|sell-?off|war|invasion|pandemic|attack)", "broad_selloff"),
    )

    def suggest(self, description: str, sectors: list[str], classes: list[str]) -> dict:
        q = description.lower()
        if re.search(r"\b(oil|opec|energy shock)", q):
            shape = {"label": "Oil price spike", "default": -0.03,
                     "sectors": {"Energy": 0.15, "Consumer Discretionary": -0.08,
                                 "Industrials": -0.06},
                     "asset_classes": {"fixed_income": -0.01}}
        else:
            sid = next((sid for pat, sid in self.RULES if re.search(pat, q)), None)
            shape = (SCENARIOS[sid] if sid else
                     {"label": "Generic market fall", "default": -0.10, "sectors": {},
                      "asset_classes": {}})
        sec = {s: v for s, v in shape["sectors"].items() if s in sectors}
        cls = {c: v for c, v in shape.get("asset_classes", {}).items() if c in classes}
        return {
            "label": shape["label"],
            "sector_shocks": [{"sector": s, "shock": v, "reason": f"{s} moves {_pct(v)} under this scenario."}
                              for s, v in sec.items()],
            "asset_class_shocks": [{"asset_class": c, "shock": v,
                                    "reason": f"This asset class moves {_pct(v)} under this scenario."}
                                   for c, v in cls.items()],
            "default_shock": shape["default"],
            "default_reason": f"Everything else moves {_pct(shape['default'])}.",
            "rationale": ("Offline keyword suggestion (no AI model connected): an illustrative "
                          "shape to review and edit, not a forecast."),
        }
