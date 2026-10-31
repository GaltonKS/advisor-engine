"""Offline stand-in for the Anthropic client, for tests and demos.

`MockClient.messages.create` follows the same protocol chat.py drives: first a
`get_risk_profile` tool_use block, then, once the tool result is in the
conversation, a final text block. Nothing here touches the network or imports
`anthropic`; the reply still goes through the real tool, number check and audit log.

Every figure in a reply is formatted from the tool output (fractions as percents),
never typed in, so the number check passes. Wording avoids internal field names and
avoids direction words ("up", "gain", ...) near figures, which the check reads as a
stated sign.

Template choice looks at the latest user question, in this order: a holding's ticker or
"why" -> position; "client"/"explain" -> client draft; "drawdown"/"loss" -> drawdown;
"sector" -> sectors; "risk"/overview words -> overview; otherwise a fallback.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from ._paths import DEFAULT_HOLDINGS
from .numcheck import verify

# chat.py's retry message; it is a user turn but not a question.
_CORRECTION_PREFIX = "Your answer contained figures"

_INVENTED = ("47.3%", "83.7%", "61.9%", "29.4%")


# --- formatting (display only; no new figures are derived) ------------------------

def _fixed(x: float, d: int) -> str:
    """`x` to `d` decimals, adding decimals while only one significant digit shows.

    One-digit figures ("0.2") are coarse to the number check and match loosely, so
    show more of the engine's precision instead.
    """
    s = f"{x:.{d}f}"
    for extra in (1, 2):
        if len(s.replace("-", "").replace(".", "").lstrip("0")) > 1:
            break
        s = f"{x:.{d + extra}f}"
    return s


def _pct(x: float, d: int = 1) -> str:
    return _fixed(x * 100, d) + "%"


def _num(x: float, d: int = 1) -> str:
    return _fixed(x, d)


def _mult(x: float) -> str:
    return _fixed(x, 2) + "x"


def _closeness(corr: float) -> str:
    if corr >= 0.6:
        return "closely"
    if corr >= 0.3:
        return "moderately"
    return "only loosely"


# --- reading the conversation -------------------------------------------------------

def _is_question(m: dict) -> bool:
    return (
        m["role"] == "user"
        and isinstance(m["content"], str)
        and not m["content"].startswith(_CORRECTION_PREFIX)
    )


def _current_turn(messages: list[dict]) -> tuple[str, list[dict]]:
    """The latest user question and the messages after it."""
    for i in range(len(messages) - 1, -1, -1):
        if _is_question(messages[i]):
            return messages[i]["content"], messages[i + 1:]
    return "", []


def _tool_results(after: list[dict]) -> list[dict]:
    return [
        b
        for m in after
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_result"
    ]


def _holdings_path(system: str) -> str:
    m = re.search(r"holdings_path ['\"](.+?)['\"]", system or "")
    return m.group(1) if m else str(DEFAULT_HOLDINGS)


def _tone(system: str) -> str:
    m = re.search(r"^- Tone: (.+)\.$", system or "", re.MULTILINE)
    return m.group(1) if m else ""


def _objective(system: str) -> str:
    m = re.search(r"client objective is: (.+?)\. Use it", system or "")
    return m.group(1) if m else ""


def _find_position(question: str, positions: list[dict]) -> dict | None:
    for p in positions:
        t = p["ticker"]
        if len(t) < 2:  # a single letter matches ordinary words
            continue
        pat = rf"\b{re.escape(t)}\b"
        if re.search(pat, question, 0 if len(t) < 3 else re.IGNORECASE):
            return p
    return None


# --- reply templates -----------------------------------------------------------------

_UNCLASSIFIED = "Unclassified"  # holdings.UNCLASSIFIED; kept local, this module is display-only


def _coverage_note(d: dict) -> str:
    """A sentence on holdings with no sector, or "" when every holding has one."""
    cov = d.get("sector_coverage") or {}
    if not cov.get("n_unclassified"):
        return ""
    return (f"{_pct(cov['unclassified_weight'])} of the portfolio "
            f"({', '.join(cov['unclassified_tickers'])}) has no sector on file, so sector "
            "figures are incomplete; sectors can be added in the Holdings tab.")


def _classified_sectors(d: dict) -> list[tuple[str, float]]:
    return [(s, w) for s, w in d["sector_exposure"].items() if s != _UNCLASSIFIED]


def _data_note(d: dict) -> str:
    return f"The returns behind this are {d['returns_source']}, so treat the picture as illustrative."


def _overview(d: dict, concise: bool, objective: str) -> str:
    c = d["concentration"]
    n, bets = c["n_positions"], c["effective_bets"]
    top = d["positions"][0]
    if bets < 0.5 * n:
        shape = "so the risk is more concentrated than the number of holdings suggests"
    elif bets < 0.8 * n:
        shape = "so it is somewhat less diversified than the number of holdings suggests"
    else:
        shape = "so the risk is spread fairly evenly across the holdings"
    out = [
        f"The portfolio holds {n} positions but behaves like about {_num(bets)} independent "
        f"bets, {shape}. {top['ticker']} is the biggest source of risk, at "
        f"{_pct(top['risk_share'])} of the portfolio's overall risk on a {_pct(top['weight'])} weight."
    ]
    if objective:
        out.append(f"I'm reading this with the firm's {objective} objective in mind.")
    if _coverage_note(d):
        out.append(_coverage_note(d))
    if not concise:
        out.append(_data_note(d))
    out.append("I can go into more detail by position, by sector, or on drawdown and stress loss.")
    return " ".join(out)


def _position(d: dict, question: str, concise: bool) -> str:
    named = _find_position(question, d["positions"])
    p = named or d["positions"][0]
    lead = "" if named else "You didn't name a position, so here is the one adding the most risk. "
    size = (
        "more risk than its size would suggest"
        if p["risk_to_weight"] > 1
        else "no more risk than its size would suggest"
        if p["risk_to_weight"] == 1
        else "less risk than its size would suggest"
    )
    out = [
        f"{lead}{p['ticker']} ({p['sector']}) is {_pct(p['weight'])} of the portfolio by value "
        f"but accounts for {_pct(p['risk_share'])} of its overall risk, which is {size}.",
        f"Relative to its size it adds {_mult(p['risk_to_weight'])} the risk, and it moves "
        f"{_closeness(p['avg_corr_to_rest'])} with the rest of the portfolio "
        f"(average correlation {_num(p['avg_corr_to_rest'], 2)}).",
    ]
    if not concise:
        out.append(_data_note(d))
    out.append("I can compare it with other positions or look at its sector if that helps.")
    return " ".join(out)


def _sectors(d: dict, concise: bool) -> str:
    items = _classified_sectors(d)
    note = _coverage_note(d)
    tail = "" if concise else " " + _data_note(d)
    if not items:
        return (f"None of the holdings has a sector on file, so I can't describe sector "
                f"exposure yet. {note}{tail}")
    shown = items[:5]
    lines = "\n".join(f"- {s}: {_pct(w)}" for s, w in shown)
    sl = d["concentration"]["sector_level"]
    first = f"{shown[0][0]} is the largest sector at {_pct(shown[0][1])} of the portfolio."
    if "sector_effective_bets" in sl:
        first += (f" Across {sl['n_sectors']} sectors it behaves like about "
                  f"{_num(sl['sector_effective_bets'])} independent sector bets.")
    else:
        first += " Sector-level diversification can't be assessed until every holding has a sector."
    more = "" if len(items) <= len(shown) else "\nThe remaining sectors are smaller than these."
    if note:
        more += f"\n\n{note}"
    return f"{first}\n\n{lines}{more}\n\nI can break any sector down by position.{tail}"


def _drawdown(d: dict, concise: bool) -> str:
    dd = d["drawdown"]
    label = dd["period_label"]
    out = [
        f"In the history analysed, the portfolio's worst peak-to-trough drop was "
        f"{_pct(abs(dd['historical_max_drawdown']))} on a {label} basis.",
        f"A 1-in-100 stress {label} period would bring a loss of "
        f"{_pct(abs(dd['stress_loss_1_in_100']))}.",
        f"Typical {label} volatility is {_pct(dd['period_volatility'])}.",
    ]
    if not concise:
        out.append(_data_note(d) + " Neither figure is a forecast.")
    out.append("I can show which positions contribute most to that risk.")
    return " ".join(out)


def _client_draft(d: dict, concise: bool) -> str:
    c, dd = d["concentration"], d["drawdown"]
    sectors = _classified_sectors(d)
    label = dd["period_label"]
    out = [
        "DRAFT for advisor review. Not yet sent to the client.",
        "",
        f"Your portfolio holds {c['n_positions']} investments. Many of them tend to move "
        f"together, so in practice it behaves like about {_num(c['effective_bets'])} "
        f"independent bets rather than {c['n_positions']}.",
        "",
        *([f"The largest share of your money is in {sectors[0][0]}, at "
           f"{_pct(sectors[0][1])} of the portfolio.", ""] if sectors else []),
        f"Looking at the history we analysed, the worst peak-to-trough drop was "
        f"{_pct(abs(dd['historical_max_drawdown']))} on a {label} basis. In a severe "
        f"1-in-100 {label} period, we would expect a loss of about "
        f"{_pct(abs(dd['stress_loss_1_in_100']))}.",
        "",
        "These figures describe the past and a stress scenario. They are not a forecast "
        "and not a recommendation.",
    ]
    if not concise:
        out += ["", _data_note(d)]
    return "\n".join(out)


_FALLBACK = (
    "I can answer questions about this portfolio's risk using the engine's results. "
    "Try one of these:\n"
    "- A risk overview (for example, \"How risky is this portfolio?\")\n"
    "- Why a position matters (for example, \"Why is a given ticker a risk?\")\n"
    "- Sector exposure\n"
    "- Drawdown and stress loss\n"
    "- A client-ready explanation, drafted for your review"
)

_OVERVIEW_WORDS = ("risk", "overview", "summary", "portfolio", "concentrat", "diversif", "bets")


def _choose(question: str, positions: list[dict]) -> str:
    q = question.lower()
    if _find_position(question, positions) or re.search(r"\bwhy\b", q):
        return "position"
    if "client" in q or "explain" in q:
        return "client"
    if "drawdown" in q or "loss" in q:
        return "drawdown"
    if "sector" in q:
        return "sectors"
    if any(w in q for w in _OVERVIEW_WORDS):
        return "overview"
    return "fallback"


def render_reply(
    question: str, tool_text: str, system: str = "", *, force_bad_number: bool = False
) -> str:
    """Final text for `question` given the raw tool output."""
    try:
        d = json.loads(tool_text)
    except ValueError:
        d = None
    if not isinstance(d, dict) or "positions" not in d:
        return f"I couldn't get a risk profile for that portfolio. The engine said: {tool_text}"

    concise = "concise" in _tone(system).lower()
    kind = _choose(question, d["positions"])
    if kind == "position":
        text = _position(d, question, concise)
    elif kind == "client":
        text = _client_draft(d, concise)
    elif kind == "drawdown":
        text = _drawdown(d, concise)
    elif kind == "sectors":
        text = _sectors(d, concise)
    elif kind == "overview":
        text = _overview(d, concise, _objective(system))
    else:
        text = _FALLBACK

    if force_bad_number:
        # A figure the engine never produced, for demoing the guardrail.
        bad = next(c for c in _INVENTED if not verify(c, [tool_text])["ok"])
        text += f"\n\nSimilar portfolios have historically returned about {bad} a year."
    return text


# --- the client ------------------------------------------------------------------------

class MockClient:
    """Drop-in for `anthropic.Anthropic()` as chat.py uses it: `client.messages.create(...)`."""

    model_name = "mock"

    def __init__(self, *, force_bad_number: bool = False):
        self.force_bad_number = force_bad_number
        self.calls: list[dict] = []
        self.messages = self

    def create(self, *, model=None, max_tokens=None, system="", tools=None, messages=(), **_):
        self.calls.append({"model": model, "system": system, "messages": list(messages)})
        question, after = _current_turn(list(messages))
        results = _tool_results(after)
        if not results:
            block = SimpleNamespace(
                type="tool_use",
                id=f"mock_tu_{len(self.calls)}",
                name="get_risk_profile",
                input={"holdings_path": _holdings_path(system)},
            )
            return SimpleNamespace(content=[block], stop_reason="tool_use")
        text = render_reply(
            question, results[-1]["content"], system, force_bad_number=self.force_bad_number
        )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn"
        )
