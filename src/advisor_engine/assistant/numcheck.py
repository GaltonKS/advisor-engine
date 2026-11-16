"""Guardrail: every figure in a reply must come from the engine's tool output.

`verify` pulls the numbers out of the model's reply and checks each against the
numbers in the tool results. A reply number matches an allowed number when it
equals it after rounding to the precision the reply used (21.1% matches 0.2113,
because 0.2113 as a percent is 21.13).

Deliberate rules, all aimed at not letting an invented figure through:
- A figure written with % only matches the percent form (value * 100) of a tool
  value in [-1, 1]; one written like 1.19x only matches a raw tool value; a bare
  number may match either.
- Sign is checked only when the reply states one: a leading minus, or a direction word
  (loss, drop, down vs gain, rise, up, ...) within 4 words of the number. A stated
  direction must agree with the tool value ("a 12% loss" matches -0.12, "a 12% gain"
  does not). A bare "34.1%" with no direction word still matches -0.341.
- Coarse figures (integers, one significant figure) are easy to hit by accident, so they
  need an exact tool value, a tool list length, or a tool value that rounds to them and
  is the closest tool number (within a quarter of the rounding unit). Otherwise they are
  flagged "coarse match, unverified".
- The number of items in a tool list (e.g. 12 positions) counts as a tool figure.
- Numbers in error text from a tool count, since the model may quote them.
- A K / M / B suffix scales the figure ($13.9K is 13,900) and the precision with it, so
  $13.9K is checked to the nearest 100. A scaled figure is money or a count: it only
  matches raw tool values, never the percent form.
- Labels are not figures: the 1-in-100 metric name, account types (401(k), 403b) and
  calendar dates (Nov 10, 10 November). They are blanked before numbers are read.
Spelled-out numbers ("twelve") are not detected.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_TOKEN = re.compile(
    rf"(?<![\w.])(?P<sign>[-−])?(?P<num>{_NUM})"
    r"(?P<suffix>\s?%|\s?percent\b|[x×](?!\w)|[kKmMbB](?!\w))?(?![\w]|\.\d)"
)
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
# "1." / "2)" / "**3.**" at the start of a line, optionally after a bullet marker.
_LIST_NUMBER = re.compile(r"^(\s*(?:[>*+-]\s+)?(?:\*\*)?)\d{1,3}[.)](?=[\s*])")
# Labels, not figures: the name of the engine's 1-in-100 stress metric, US retirement
# account types (401(k), 403b; without this the K / B suffix would read 401k as 401,000)
# and calendar dates in either order (Nov 10, Sept. 3, 10 November). A day must not be
# followed by more digits, so "Nov 2026" leaves the year to the year rule. "May" must be
# capitalised so "10 may be" is not taken for a date and a real figure hidden.
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?(?![\d%×]|\.\d|,\d|\s?percent\b)"
_LABEL = re.compile(
    r"\b1[- ]in[- ]100\b"
    r"|(?<![\w$.,])40[13]\s?(?:\([kb]\)|[kb]\b)"
    rf"|\b{_MONTH}\s+{_DAY}"
    rf"|(?<![\w$.,])\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Token:
    text: str
    value: float  # as written: "21.1%" -> 21.1
    decimals: int
    kind: str  # "percent" | "multiplier" | "bare"
    direction: int = 0  # -1 / +1 if the reply states a sign for it, else 0
    coarse: bool = False  # integer or one significant figure
    scale: float = 1.0  # K / M / B multiplier written after the number


_NEG_WORDS = re.compile(
    r"(?:loss|losses|lose|loses|losing|lost|drop|drops|dropped|decline|declines|declined|"
    r"drawdown|drawdowns|down|fell|fall|falls|falling)$"
)
_POS_WORDS = re.compile(
    r"(?:gain|gains|gained|rise|rises|rose|risen|up|increase|increases|increased)$"
)
_DIRECTION_WINDOW = 4
_CONTEXT_STOPS = {"and", "but", "while", "whereas", "versus", "vs"}
_CONTEXT_PIECE = re.compile(r"[A-Za-z']+|[,;:.!?()\n]")


def _direction_near(before: str, after: str) -> int:
    """Direction word within 4 words either side, in the same clause; nearest wins, ties go
    to the word before the number."""
    best: tuple[int, int, int] | None = None  # (distance, side, direction)
    for side, seg in ((0, before), (1, after)):
        pieces = _CONTEXT_PIECE.findall(re.sub(r"\d[\d,]*(?:\.\d+)?", " ", seg).lower())
        if side == 0:
            pieces.reverse()
        n = 0
        for piece in pieces:
            if not piece[0].isalpha() or piece in _CONTEXT_STOPS:
                break
            n += 1
            if n > _DIRECTION_WINDOW:
                break
            d = -1 if _NEG_WORDS.match(piece) else 1 if _POS_WORDS.match(piece) else 0
            if d:
                cand = (n, side, d)
                if best is None or cand < best:
                    best = cand
                break
    return best[2] if best else 0


def _is_coarse(num: str, decimals: int) -> bool:
    if decimals == 0:
        return True
    digits = num.replace(",", "").replace(".", "").lstrip("0")
    return len(digits) <= 1


def _tokens(text: str) -> list[Token]:
    lines = []
    for line in text.splitlines():
        line = _LIST_NUMBER.sub(lambda m: " " * len(m.group(0)), line)
        lines.append(_LABEL.sub(lambda m: " " * len(m.group(0)), line))
    out = []
    cleaned = "\n".join(lines)
    for m in _TOKEN.finditer(cleaned):
        num, suffix = m.group("num"), m.group("suffix")
        decimals = len(num.split(".")[1]) if "." in num else 0
        scale = _SCALE.get((suffix or "").strip().lower(), 1.0)
        value = float(num.replace(",", "")) * scale
        # A bare 4-digit integer in 1900-2100 is a year.
        if not suffix and not m.group("sign") and decimals == 0 and "," not in num \
                and len(num) == 4 and 1900 <= value <= 2100:
            continue
        direction = 0
        if m.group("sign"):
            value = -value
            direction = -1
        else:
            direction = _direction_near(
                cleaned[max(0, m.start() - 120):m.start()], cleaned[m.end():m.end() + 120]
            )
        if suffix is None or scale != 1.0:
            kind = "bare"  # a scaled figure is money or a count, never a percent
        elif suffix.strip() in ("x", "×"):
            kind = "multiplier"
        else:
            kind = "percent"
        out.append(Token(m.group(0).strip(), value, decimals, kind, direction,
                         _is_coarse(num, decimals), scale))
    return out


def extract_numbers(text: str) -> list[float]:
    """Numeric values in `text`, as written (a percent keeps its percent scale)."""
    return [t.value for t in _tokens(text)]


def _walk(obj, raw: list[float], lengths: set[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            raw.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v, raw, lengths)
    elif isinstance(obj, (list, tuple)):
        if obj:
            raw.append(float(len(obj)))
            lengths.add(float(len(obj)))
        for v in obj:
            _walk(v, raw, lengths)


def _collect(tool_outputs) -> tuple[list[float], list[float], set[float]]:
    raw: list[float] = []
    lengths: set[float] = set()
    for out in tool_outputs:
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except ValueError:
                raw.extend(extract_numbers(out))  # plain-text tool error
                continue
        _walk(out, raw, lengths)
    pct = [v * 100 for v in raw if -1 <= v <= 1]
    return raw, pct, lengths


def collect_allowed_numbers(tool_outputs) -> list[float]:
    """Every number in the tool outputs, plus the percent form of any in [-1, 1].

    `tool_outputs` is an iterable of JSON strings, parsed objects, or error text.
    """
    raw, pct, _ = _collect(tool_outputs)
    return sorted(set(raw) | set(pct))


def _sign_ok(direction: int, a: float) -> bool:
    return direction == 0 or a == 0 or (a < 0) == (direction < 0)


def _check(tok: Token, raw, pct, lengths, rel_tol: float, abs_tol: float) -> str | None:
    """None if the token is backed by a tool value, else the reason it is not."""
    pools = {"percent": [pct], "multiplier": [raw], "bare": [raw, pct]}[tok.kind]
    if tok.scale != 1.0:
        pools = [raw]  # $21.1K is not 21.1% of anything
    x = abs(tok.value)
    # The written unit carries the multiplier: $13.9K is precise to the nearest 100.
    unit = (10 ** -tok.decimals) * tok.scale
    half_unit = 0.5 * unit

    if tok.coarse:
        # Exact equality or a list length is solid evidence. Rounding is not, unless the
        # tool value is the nearest one and within a quarter unit (tighter than rounding).
        strict = min(0.5 * tok.scale, 0.25 * unit)
        pool_all = [a for pool in pools for a in pool]
        nearest = min((abs(abs(a) - x) for a in pool_all), default=None)
        hits = [
            a for a in pool_all
            if abs(abs(a) - x) <= 1e-9
            or (abs(abs(a) - x) <= strict + 1e-9 and abs(abs(a) - x) <= nearest + 1e-9)
        ]
        if x in lengths and tok.kind != "percent":
            return None  # a count has no direction
        if hits:
            return None if any(_sign_ok(tok.direction, a) for a in hits) else "sign mismatch"
        loose = any(
            abs(x - abs(a)) <= max(half_unit, min(abs_tol, rel_tol * abs(a))) + 1e-9
            for a in pool_all
        )
        return "coarse match, unverified" if loose else "no matching tool figure"

    hits = []
    for pool in pools:
        for a in pool:
            # Rounding to the reply's precision is the real test; rel/abs tol are a
            # small floor for float noise when the reply is more precise than that.
            tol = max(half_unit, min(abs_tol, rel_tol * abs(a)))
            if abs(x - abs(a)) <= tol + 1e-9:
                hits.append(a)
    if not hits:
        return "no matching tool figure"
    return None if any(_sign_ok(tok.direction, a) for a in hits) else "sign mismatch"


def verify(reply_text: str, tool_outputs, rel_tol: float = 0.005, abs_tol: float = 0.0005) -> dict:
    """Check every number in the reply against the tool outputs.

    Returns {"ok": bool, "unmatched": [reply text of each failing number], "checked": int,
    "reasons": {reply text: why it failed}}. Reasons: "sign mismatch",
    "coarse match, unverified", "no matching tool figure".
    """
    raw, pct, lengths = _collect(tool_outputs)
    toks = _tokens(reply_text)
    unmatched: list[str] = []
    reasons: dict[str, str] = {}
    for t in toks:
        reason = _check(t, raw, pct, lengths, rel_tol, abs_tol)
        if reason and t.text not in unmatched:
            unmatched.append(t.text)
            reasons[t.text] = reason
    return {"ok": not unmatched, "unmatched": unmatched, "checked": len(toks), "reasons": reasons}
