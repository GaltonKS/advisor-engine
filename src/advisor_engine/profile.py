"""Three-part client risk profile: willingness (per person), ability (per household) and
need (per plan), each estimated from the adviser's recorded answers with an uncertainty
band, plus the ceiling and flags the engine derives from them.

DRAFT METHOD. Every number it uses (option values, noise levels, mapping tables,
thresholds, the provisional rule) lives in `profile_config.py` and is a placeholder
pending compliance review; `METHOD_STATUS` says so wherever a result is shown.

Answers, not scores, are stored: this module re-scores them on every call. Inputs are
rows of {person_id, question_id, answer, recorded_at}. The latest row per
(person_id, question_id) wins, and a null answer retracts the question.

Estimator (per dimension, per person for willingness). Each answer is a noisy reading
x_i of a latent score on the 1-10 scale, with noise `noise_sd_i` from the question bank:

    w_i       = 1 / noise_sd_i^2
    mean      = sum(w_i x_i) / sum(w_i)
    post_var  = 1 / sum(w_i)                         (shrinks as answers accumulate)
    spread    = sum(w_i (x_i - mean)^2) / sum(w_i)   (grows when answers disagree)
    band      = sqrt(post_var + spread)

`band` is reported as a ± half-width, with `low` / `high` = mean ∓ band clipped to the
scale. A dimension with no answers is `None`, never zero. This is a simple heuristic,
not a calibrated Bayesian model: the noise levels are guesses and readings from
different questions are treated as independent.

Household rules:
- Household willingness is the lower of the people's willingness means (among people
  with answers). Partners are never averaged.
- Ceiling = min(household willingness, ability); ability is skipped while it has no
  answers, and the ceiling is `None` until there is at least one willingness answer.
- Need is compared with the ceiling, never folded into it.
- Flags are template text from the figures above; the engine flags a mismatch, it does
  not say what to do about it.

Every figure is rounded half up to one decimal before it is compared or reported, so a
flag fires exactly when the figures shown cross its threshold.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from . import profile_config as cfg

# --- answers -----------------------------------------------------------------------------


def _number(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f"{what} must be a number.")
    return float(x)


def validate_answer(question_id: str, answer: Any) -> Any:
    """Check `answer` against the question bank and return it in its stored form.

    choice: an option index (int); loss_limits: {"uncomfortable_pct", "act_pct"} as
    fractions within the question's range with act_pct >= uncomfortable_pct; number: a
    number within the question's [min, max]. `None` (a retraction) is always valid.
    Raises ValueError with a message fit to show the adviser.
    """
    q = cfg.QUESTIONS_BY_ID.get(question_id)
    if q is None:
        raise ValueError(f"Unknown question: {question_id}")
    if answer is None:
        return None
    if q["kind"] == "choice":
        n = len(q["options"])
        if isinstance(answer, bool) or not isinstance(answer, int) or not 0 <= answer < n:
            raise ValueError(f"Answer to {question_id} must be an option index 0-{n - 1}.")
        return answer
    if q["kind"] == "loss_limits":
        if not isinstance(answer, dict) or set(answer) != {"uncomfortable_pct", "act_pct"}:
            raise ValueError(f"Answer to {question_id} must give uncomfortable_pct and act_pct.")
        out = {k: _number(answer[k], f"{question_id}.{k}") for k in ("uncomfortable_pct", "act_pct")}
        for k, v in out.items():
            if not q["min"] <= v <= q["max"]:
                raise ValueError(f"{question_id}.{k} must be between {q['min']} and {q['max']} "
                                 "(a fraction, e.g. 0.2 for 20%).")
        if out["act_pct"] < out["uncomfortable_pct"]:
            raise ValueError("The fall at which they would act cannot be smaller than the "
                             "fall at which they become uncomfortable.")
        return out
    if q["kind"] == "number":
        v = _number(answer, f"Answer to {question_id}")
        if not q["min"] <= v <= q["max"]:
            raise ValueError(f"Answer to {question_id} must be between {q['min']} and {q['max']}.")
        return v
    raise ValueError(f"Question {question_id} has an unknown kind.")  # pragma: no cover


def interpolate(table: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear lookup; values beyond the first or last knot take its value."""
    if x <= table[0][0]:
        return float(table[0][1])
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(table[-1][1])


def reading(question_id: str, answer: Any) -> float:
    """The 1-10 reading a (validated, non-null) answer gives."""
    q = cfg.QUESTIONS_BY_ID[question_id]
    answer = validate_answer(question_id, answer)
    if answer is None:
        raise ValueError("A retracted answer has no reading.")
    if q["kind"] == "choice":
        return float(q["options"][answer]["value"])
    if q["kind"] == "loss_limits":
        return interpolate(q["table"], answer["act_pct"])
    return interpolate(q["table"], answer)


def latest_answers(inputs: Iterable[dict]) -> list[dict]:
    """The answers in force: the latest row per (person_id, question_id), retractions
    dropped. Rows are ordered by `recorded_at` when every row has one (stable, so rows
    recorded at the same instant keep their given order), else taken in the given order.
    Rows for questions not in the bank are ignored."""
    rows = list(inputs)
    if rows and all(r.get("recorded_at") is not None for r in rows):
        rows = sorted(rows, key=lambda r: r["recorded_at"])
    current: dict[tuple, dict] = {}
    for r in rows:
        if r["question_id"] not in cfg.QUESTIONS_BY_ID:
            continue
        key = (r.get("person_id"), r["question_id"])
        current.pop(key, None)  # re-insert so the order is the order of the latest answers
        current[key] = r
    return [r for r in current.values() if r.get("answer") is not None]


def inputs_hash(inputs: Iterable[dict]) -> str:
    """SHA-256 of the answers in force, in a stable order, so two snapshots made from
    the same answers carry the same hash whatever order the rows arrived in."""
    effective = sorted(
        ({"person_id": None if r.get("person_id") is None else str(r["person_id"]),
          "question_id": r["question_id"], "answer": r["answer"]}
         for r in latest_answers(inputs)),
        key=lambda r: (r["person_id"] or "", r["question_id"]))
    blob = json.dumps(effective, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# --- estimator ---------------------------------------------------------------------------


def r1(x: float) -> float:
    """Round half up to one decimal (Python's round() sends halves to the even side)."""
    return float(math.floor(x * 10 + 0.5 + 1e-9) / 10)


def estimate(readings: list[tuple[float, float]]) -> dict | None:
    """Combine (reading, noise_sd) pairs into {mean, band, low, high, n_answers}; see
    the module docstring for the formula. `None` when there are no readings."""
    if not readings:
        return None
    weights = [1.0 / sd ** 2 for _, sd in readings]
    total = sum(weights)
    mean = sum(w * x for w, (x, _) in zip(weights, readings)) / total
    spread = sum(w * (x - mean) ** 2 for w, (x, _) in zip(weights, readings)) / total
    band = math.sqrt(1.0 / total + spread)
    m, b = r1(mean), r1(band)
    return {"mean": m, "band": b, "low": r1(max(cfg.SCALE_MIN, m - b)),
            "high": r1(min(cfg.SCALE_MAX, m + b)), "n_answers": len(readings)}


# --- flags -------------------------------------------------------------------------------


def _f(x: float) -> str:
    return f"{x:.1f}"


def _flag(code: str, severity: str, message: str, dimensions: list[str],
          people: list[str] | None = None) -> dict:
    return {"code": code, "severity": severity, "message": message,
            "refs": {"dimensions": dimensions, "people": people or []}}


def derive_flags(people: list[dict], household: dict | None, ability: dict | None,
                 need: dict | None, ceiling: dict | None,
                 portfolio_risk: float | None = None) -> list[dict]:
    """Template flags from the estimates. `people` are {person_id, name, willingness}.
    Thresholds come from `profile_config`; each fires at or above its value."""
    flags: list[dict] = []
    answered = [p for p in people if p["willingness"] is not None]
    if len(answered) >= 2:
        a, b = sorted(answered[:2], key=lambda p: p["willingness"]["mean"])
        gap = r1(b["willingness"]["mean"] - a["willingness"]["mean"])
        if gap >= cfg.PARTNER_GAP:
            flags.append(_flag(
                "partner_gap", "warn",
                f"{a['name']} and {b['name']} differ on willingness to take risk: "
                f"{_f(a['willingness']['mean'])} against {_f(b['willingness']['mean'])}, "
                f"a gap of {_f(gap)}. The household figure uses the lower, "
                f"{_f(a['willingness']['mean'])}.",
                ["willingness"], [a["person_id"], b["person_id"]]))
    if ceiling is not None and need is not None:
        gap = r1(need["mean"] - ceiling["value"])
        if gap >= cfg.NEED_OVER_CEILING:
            flags.append(_flag(
                "need_exceeds_ceiling", "warn",
                f"The plan needs {_f(need['mean'])} on the 1 to 10 scale, {_f(gap)} above "
                f"the ceiling of {_f(ceiling['value'])}.",
                ["need", "ceiling"]))
    if ceiling is not None and portfolio_risk is not None:
        gap = r1(portfolio_risk - ceiling["value"])
        if gap >= cfg.PORTFOLIO_OVER_CEILING:
            flags.append(_flag(
                "portfolio_exceeds_ceiling", "warn",
                f"The portfolio's risk reading of {_f(portfolio_risk)} is {_f(gap)} above "
                f"the ceiling of {_f(ceiling['value'])}.",
                ["portfolio", "ceiling"]))
    if household is not None and ability is not None:
        gap = r1(household["mean"] - ability["mean"])
        if gap >= cfg.ABILITY_BELOW_WILLINGNESS:
            flags.append(_flag(
                "ability_below_willingness", "info",
                f"Ability to take risk ({_f(ability['mean'])}) is {_f(gap)} below "
                f"willingness ({_f(household['mean'])}), so ability sets the ceiling.",
                ["ability", "willingness"]))
    wide = [(f"Willingness for {p['name']}", "willingness", [p["person_id"]], p["willingness"])
            for p in answered]
    wide += [("Ability", "ability", [], ability), ("Need", "need", [], need)]
    for label, dim, refs, est in wide:
        if est is not None and est["band"] >= cfg.WIDE_BAND:
            flags.append(_flag(
                "wide_band", "info",
                f"{label} is uncertain: {_f(est['mean'])} ± {_f(est['band'])}. More "
                "answers, or answers that agree, will narrow it.",
                [dim], refs))
    return flags


# --- the profile -------------------------------------------------------------------------


def _describe(est: dict | None) -> str:
    return "no answers yet" if est is None else f"{_f(est['mean'])} (± {_f(est['band'])})"


def compute_profile(inputs: Iterable[dict], people: list[dict],
                    portfolio_risk: float | None = None) -> dict:
    """Score a household's answers into the three-part profile.

    `inputs`: answer rows {person_id, question_id, answer, recorded_at} (all of them;
    latest wins, null retracts). `people`: [{id, name, role}], one or two.
    `portfolio_risk`: an optional portfolio risk reading on the same 1-10 scale, supplied
    by the caller; this function never computes it. Deterministic; raises ValueError for
    a stored answer the question bank rejects, a willingness answer without a person (or
    for someone not in `people`), or more than two people.
    """
    if len(people) > 2:
        raise ValueError("A household has at most two people.")
    if portfolio_risk is not None:
        portfolio_risk = _number(portfolio_risk, "portfolio_risk")
        if not cfg.SCALE_MIN <= portfolio_risk <= cfg.SCALE_MAX:
            raise ValueError("portfolio_risk must be on the 1 to 10 scale.")
        portfolio_risk = r1(portfolio_risk)
    ids = [str(p["id"]) for p in people]
    readings: dict[tuple[str, str | None], list[tuple[float, float]]] = {}
    answers = []
    for row in latest_answers(inputs):
        q = cfg.QUESTIONS_BY_ID[row["question_id"]]
        pid = None if row.get("person_id") is None else str(row["person_id"])
        if q["dimension"] == "willingness":
            if pid not in ids:
                raise ValueError(f"Willingness answer {q['id']} is not for a person in this "
                                 "household.")
        elif pid is not None:
            raise ValueError(f"{q['id']} is a household question; it takes no person.")
        x = reading(q["id"], row["answer"])
        readings.setdefault((q["dimension"], pid), []).append((x, q["noise_sd"]))
        answers.append({"person_id": pid, "question_id": q["id"], "dimension": q["dimension"],
                        "answer": row["answer"], "reading": r1(x)})

    persons = [{"person_id": str(p["id"]), "name": p["name"], "role": p.get("role"),
                "willingness": estimate(readings.get(("willingness", str(p["id"])), []))}
               for p in people]
    answered = [p for p in persons if p["willingness"] is not None]
    household = None
    if answered:
        low = min(answered, key=lambda p: p["willingness"]["mean"])  # first on a tie
        household = {**low["willingness"], "person_id": low["person_id"]}
    ability = estimate(readings.get(("ability", None), []))
    need = estimate(readings.get(("need", None), []))

    ceiling = None
    if household is not None:
        if ability is not None and ability["mean"] < household["mean"]:
            ceiling = {"value": ability["mean"], "limited_by": "ability"}
        else:
            ceiling = {"value": household["mean"], "limited_by": "willingness"}

    missing = []
    if not persons:
        missing.append("No people in the household yet.")
    for p in persons:
        n = 0 if p["willingness"] is None else p["willingness"]["n_answers"]
        if n < cfg.MIN_WILLINGNESS_ANSWERS:
            missing.append(f"{p['name']} needs at least {cfg.MIN_WILLINGNESS_ANSWERS} "
                           "willingness answers.")
    for dim, est, need_n in (("Ability", ability, cfg.MIN_ABILITY_ANSWERS),
                             ("Need", need, cfg.MIN_NEED_ANSWERS)):
        if (0 if est is None else est["n_answers"]) < need_n:
            missing.append(f"{dim} needs at least {need_n} answer.")

    flags = derive_flags(persons, household, ability, need, ceiling, portfolio_risk)

    parts = []
    if persons:
        parts.append("Willingness: " + "; ".join(
            f"{p['name']} {_describe(p['willingness'])}" for p in persons) + ".")
        if household is not None and len(answered) > 1:
            parts.append(f"The household uses the lower, {_f(household['mean'])}.")
    else:
        parts.append("Willingness: no people in the household yet.")
    parts.append(f"Ability: {_describe(ability)}.")
    parts.append(f"Need: {_describe(need)}.")
    if ceiling is None:
        parts.append("Ceiling: not set until there is a willingness answer.")
    else:
        parts.append(f"Ceiling: {_f(ceiling['value'])}, set by {ceiling['limited_by']}.")
    if missing:
        parts.append("Provisional: more answers are needed.")
    if flags:
        parts.append(f"{len(flags)} flag{'s' if len(flags) != 1 else ''} to review.")
    parts.append(cfg.METHOD_STATUS)

    return {
        "people": persons,
        "household_willingness": household,
        "ability": ability,
        "need": need,
        "ceiling": ceiling,
        "portfolio_risk": portfolio_risk,
        "provisional": bool(missing),
        "provisional_reasons": missing,
        "flags": flags,
        "answers": answers,
        "plain_summary": " ".join(parts),
        "scale": {"min": cfg.SCALE_MIN, "max": cfg.SCALE_MAX},
        "method_version": cfg.METHOD_VERSION,
        "method_status": cfg.METHOD_STATUS,
    }
