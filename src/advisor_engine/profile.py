"""Household risk profile: willingness, ability and need, with uncertainty bands.

Draft method (`profile_config.METHOD_STATUS` says so wherever it is shown; every number
lives in `profile_config` and is a placeholder pending compliance review):

Inputs are the stored answers, one row per answer: `person_id`, `dimension`,
`question_id`, `answer`, `recorded_at`. Answers are scored here on every read; nothing
stores a score. For each (person, question) the latest row wins, by `recorded_at`, ties
kept in the order given; a latest `answer` of None means the answer was retracted and the
question counts as unanswered.

Each answer is a noisy reading x_i of a latent 1 to 10 score, with the question's
`noise_sd` s_i. Per dimension (per person for willingness):

    w_i       = 1 / s_i²
    mean      = Σ w_i x_i / Σ w_i
    posterior = 1 / Σ w_i                          (shrinks as answers accumulate)
    spread    = Σ w_i (x_i - mean)² / Σ w_i        (grows when answers disagree)
    band      = sqrt(posterior + spread)           (reported as mean ± band)

A dimension with no answers is None, never zero. This is a deliberately simple
precision-weighted average, not a calibrated model: the noise levels are guesses and the
band is a reading of how settled the answers are, not a confidence interval.

Household rules:
- household willingness is the lower of the people's willingness means (among people
  with willingness answers); partners are never averaged.
- ceiling = min(household willingness, ability), or household willingness while ability
  is unanswered; None until there is a willingness answer.
- need is compared with the ceiling, and so is an optional portfolio risk reading on the
  same 1 to 10 scale (supplied by the caller; this module does not compute it).
- provisional unless every person has `MIN_WILLINGNESS_ANSWERS_PER_PERSON` willingness
  answers and ability and need meet their minimums.

Flags and the plain summary are filled-in templates; no model writes them.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from . import profile_config as cfg

_EPS = 1e-9  # threshold comparisons tolerate float noise from the mapping tables
_QUESTIONS = {q["id"]: q for q in cfg.QUESTIONS}


# --- the question bank ------------------------------------------------------------------

def question_bank() -> dict:
    """The questions as plain data (for the UI), with the method version and status."""
    return {
        "questions": json.loads(json.dumps(cfg.QUESTIONS)),
        "method_version": cfg.METHOD_VERSION,
        "method_status": cfg.METHOD_STATUS,
    }


def question(question_id: str) -> dict:
    try:
        return _QUESTIONS[question_id]
    except KeyError:
        raise ValueError(f"Unknown profile question: {question_id!r}.") from None


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_answer(question_id: str, answer: Any) -> None:
    """Raise ValueError unless `answer` is a valid answer to the question (None retracts)."""
    q = question(question_id)
    if answer is None:
        return
    kind = q["kind"]
    if kind == "choice":
        keys = [o["key"] for o in q["options"]]
        if answer not in keys:
            raise ValueError(f"Answer to {question_id} must be one of {keys}.")
    elif kind == "number":
        if not _is_number(answer) or not q["min"] <= answer <= q["max"]:
            raise ValueError(f"Answer to {question_id} must be a number from "
                             f"{q['min']:g} to {q['max']:g}.")
    elif kind == "loss_limits":
        if not isinstance(answer, dict) or set(answer) != {"uncomfortable_pct", "act_pct"}:
            raise ValueError(f"Answer to {question_id} must give uncomfortable_pct and act_pct.")
        for k in ("uncomfortable_pct", "act_pct"):
            v = answer[k]
            if not _is_number(v) or not q["min"] <= v <= q["max"]:
                raise ValueError(f"{k} must be a fraction from {q['min']:g} to {q['max']:g}.")
        if answer["act_pct"] < answer["uncomfortable_pct"]:
            raise ValueError("act_pct must be at least uncomfortable_pct: they would act "
                             "only at or beyond the fall that makes them uncomfortable.")
    else:  # pragma: no cover - the bank is static
        raise ValueError(f"Unknown question kind {kind!r}.")


def interpolate(table: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear lookup in `table` ((x, score) pairs, x ascending), clamped."""
    if x <= table[0][0]:
        return float(table[0][1])
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(table[-1][1])


def score_answer(question_id: str, answer: Any) -> float:
    """The 1 to 10 reading one (valid, non-null) answer gives."""
    validate_answer(question_id, answer)
    if answer is None:
        raise ValueError("A retracted answer has no score.")
    q = question(question_id)
    if q["kind"] == "choice":
        return float(next(o["value"] for o in q["options"] if o["key"] == answer))
    if q["kind"] == "loss_limits":
        return interpolate(q["table"], float(answer["act_pct"]))
    return interpolate(q["table"], float(answer))


# --- resolving inputs ----------------------------------------------------------------------

def latest_answers(inputs: Iterable[dict]) -> list[dict]:
    """The effective answers: latest row per (person, question), retractions dropped.

    Rows are ordered by `recorded_at` (ties keep the order given, which is insertion
    order from the stores). Returned in a stable order: dimension, person, question.
    """
    latest: dict[tuple, dict] = {}
    for row in sorted(inputs, key=lambda r: r["recorded_at"]):
        latest[(row.get("person_id"), row["question_id"])] = row
    live = [r for r in latest.values() if r.get("answer") is not None]
    order = {d: i for i, d in enumerate(cfg.DIMENSIONS)}
    return sorted(live, key=lambda r: (order.get(r["dimension"], 99), str(r.get("person_id") or ""),
                                       r["question_id"]))


def _canonical(row: dict) -> dict:
    return {"dimension": row["dimension"], "person_id": row.get("person_id"),
            "question_id": row["question_id"], "answer": row["answer"]}


def inputs_hash(inputs: Iterable[dict]) -> str:
    """SHA-256 of the effective answers (after latest-wins and retractions), stable order.

    Two input histories that resolve to the same answers hash the same, so a snapshot's
    hash says which answers it was computed from, not how they were entered.
    """
    rows = [_canonical(r) for r in latest_answers(inputs)]
    blob = json.dumps({"method_version": cfg.METHOD_VERSION, "answers": rows},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# --- the estimator --------------------------------------------------------------------------

def estimate(readings: list[tuple[float, float]]) -> dict | None:
    """Precision-weighted mean and band from (score, noise_sd) readings; None if empty."""
    if not readings:
        return None
    weights = [1.0 / (sd * sd) for _, sd in readings]
    total = sum(weights)
    mean = sum(w * x for w, (x, _) in zip(weights, readings)) / total
    spread = sum(w * (x - mean) ** 2 for w, (x, _) in zip(weights, readings)) / total
    return {"mean": float(mean), "band": float(math.sqrt(1.0 / total + spread)),
            "n_answers": len(readings)}


def _rounded(est: dict | None, **extra) -> dict | None:
    if est is None:
        return None
    return {"mean": round(est["mean"], 3), "band": round(est["band"], 3),
            "n_answers": est["n_answers"], **extra}


def _ge(a: float, b: float) -> bool:
    return a >= b - _EPS


def _fmt(x: float) -> str:
    return f"{x:.1f}"


# --- the profile ----------------------------------------------------------------------------

def compute_profile(inputs: Iterable[dict], people: list[dict],
                    portfolio_risk: float | None = None) -> dict:
    """The household risk profile from stored answers. Deterministic and pure.

    `inputs`: rows with person_id, dimension, question_id, answer, recorded_at.
    `people`: [{id, name, role}], one or two (none is allowed: nothing to score yet).
    `portfolio_risk`: optional portfolio risk reading on the 1 to 10 scale.
    Raises ValueError for an answer the question bank rejects, a dimension that doesn't
    match its question, or a willingness answer for someone not in `people`.
    """
    if portfolio_risk is not None and (
            not _is_number(portfolio_risk) or not cfg.SCALE_MIN <= portfolio_risk <= cfg.SCALE_MAX):
        raise ValueError("portfolio_risk must be a number from 1 to 10.")
    people = sorted(people, key=lambda p: (p.get("role") != "primary", str(p["id"])))
    ids = {str(p["id"]) for p in people}

    readings: dict[str, list[tuple[float, float]]] = {"ability": [], "need": []}
    per_person: dict[str, list[tuple[float, float]]] = {str(p["id"]): [] for p in people}
    for row in latest_answers(inputs):
        q = question(row["question_id"])
        if row["dimension"] != q["dimension"]:
            raise ValueError(f"{row['question_id']} is a {q['dimension']} question, "
                             f"not {row['dimension']}.")
        reading = (score_answer(row["question_id"], row["answer"]), float(q["noise_sd"]))
        if q["dimension"] in cfg.PER_PERSON:
            pid = str(row.get("person_id"))
            if pid not in ids:
                raise ValueError(f"Willingness answer for unknown person {row.get('person_id')!r}.")
            per_person[pid].append(reading)
        else:
            readings[q["dimension"]].append(reading)

    raw_w = {pid: estimate(r) for pid, r in per_person.items()}
    ability = estimate(readings["ability"])
    need = estimate(readings["need"])

    answered = [p for p in people if raw_w[str(p["id"])] is not None]
    household = None
    if answered:
        low = min(answered, key=lambda p: raw_w[str(p["id"])]["mean"])  # ties: primary first
        household = {**raw_w[str(low["id"])], "person_id": low["id"]}

    ceiling = None
    if household is not None:
        if ability is not None and ability["mean"] < household["mean"]:
            ceiling = {"value": ability["mean"], "band": ability["band"], "limited_by": "ability"}
        else:
            ceiling = {"value": household["mean"], "band": household["band"],
                       "limited_by": "willingness", "person_id": household["person_id"]}

    provisional = not (
        people
        and all(len(per_person[str(p["id"])]) >= cfg.MIN_WILLINGNESS_ANSWERS_PER_PERSON
                for p in people)
        and len(readings["ability"]) >= cfg.MIN_ABILITY_ANSWERS
        and len(readings["need"]) >= cfg.MIN_NEED_ANSWERS
    )

    names = {str(p["id"]): p["name"] for p in people}
    flags = _flags(people, names, raw_w, household, ability, need, ceiling, portfolio_risk)

    return {
        "people": [{"id": p["id"], "name": p["name"], "role": p.get("role"),
                    "willingness": _rounded(raw_w[str(p["id"])])} for p in people],
        "household_willingness": _rounded(household, person_id=household["person_id"])
        if household else None,
        "ability": _rounded(ability),
        "need": _rounded(need),
        "ceiling": {**ceiling, "value": round(ceiling["value"], 3),
                    "band": round(ceiling["band"], 3)} if ceiling else None,
        "portfolio_risk": portfolio_risk,
        "provisional": provisional,
        "flags": flags,
        "plain_summary": _summary(people, raw_w, household, ability, need, ceiling,
                                  provisional, names),
        "method_version": cfg.METHOD_VERSION,
        "method_status": cfg.METHOD_STATUS,
    }


def _flag(code: str, message: str, dimensions: list[str], person_ids: list | None = None) -> dict:
    return {"code": code, "severity": cfg.FLAG_SEVERITY[code], "message": message,
            "refs": {"dimensions": dimensions, "person_ids": person_ids or []}}


def _flags(people, names, raw_w, household, ability, need, ceiling, portfolio_risk) -> list[dict]:
    out = []
    answered = [p for p in people if raw_w[str(p["id"])] is not None]
    if len(answered) == 2:
        a, b = (raw_w[str(p["id"])]["mean"] for p in answered)
        if _ge(abs(a - b), cfg.PARTNER_GAP):
            pa, pb = answered
            out.append(_flag(
                "partner_gap",
                f"{pa['name']} and {pb['name']} differ in willingness by {_fmt(abs(a - b))} "
                f"points ({_fmt(a)} and {_fmt(b)}). The household uses the lower.",
                ["willingness"], [pa["id"], pb["id"]]))
    if ceiling is not None and need is not None and _ge(need["mean"] - ceiling["value"],
                                                        cfg.NEED_OVER_CEILING):
        out.append(_flag(
            "need_exceeds_ceiling",
            f"The plan needs {_fmt(need['mean'])}, above the ceiling of "
            f"{_fmt(ceiling['value'])}.", ["need", "ceiling"]))
    if ceiling is not None and portfolio_risk is not None and _ge(
            portfolio_risk - ceiling["value"], cfg.PORTFOLIO_OVER_CEILING):
        out.append(_flag(
            "portfolio_exceeds_ceiling",
            f"The portfolio reads {_fmt(portfolio_risk)}, above the ceiling of "
            f"{_fmt(ceiling['value'])}.", ["portfolio", "ceiling"]))
    if household is not None and ability is not None and _ge(
            household["mean"] - ability["mean"], cfg.ABILITY_BELOW_WILLINGNESS):
        out.append(_flag(
            "ability_below_willingness",
            f"Ability ({_fmt(ability['mean'])}) is well below household willingness "
            f"({_fmt(household['mean'])}), so ability sets the ceiling.",
            ["ability", "willingness"]))
    wide = [(f"{names[str(p['id'])]}'s willingness", "willingness", p["id"], raw_w[str(p["id"])])
            for p in people]
    wide += [("ability", "ability", None, ability), ("need", "need", None, need)]
    wide = [w for w in wide if w[3] is not None and w[3]["band"] > cfg.WIDE_BAND + _EPS]
    if wide:
        parts = ", ".join(f"{label} (±{_fmt(est['band'])})" for label, _, _, est in wide)
        out.append(_flag(
            "wide_band", f"Uncertain: {parts}. More answers would narrow this.",
            sorted({d for _, d, _, _ in wide}, key=cfg.DIMENSIONS.index),
            [pid for _, _, pid, _ in wide if pid is not None]))
    return out


def _summary(people, raw_w, household, ability, need, ceiling, provisional, names) -> str:
    def est(e):
        return f"{_fmt(e['mean'])} (±{_fmt(e['band'])})"

    parts = []
    willing = [f"{p['name']} {est(raw_w[str(p['id'])])}" for p in people
               if raw_w[str(p["id"])] is not None]
    if not willing and ability is None and need is None:
        parts.append("No answers recorded yet.")
    else:
        if willing:
            s = "Willingness: " + ", ".join(willing) + "."
            if len(willing) == 2:
                s += f" The household uses the lower, {names[str(household['person_id'])]}'s."
            parts.append(s)
        else:
            parts.append("Willingness: not answered yet.")
        parts.append(f"Ability: {est(ability)}." if ability else "Ability: not answered yet.")
        parts.append(f"Need: {est(need)}." if need else "Need: not answered yet.")
        if ceiling:
            parts.append(f"Ceiling: {_fmt(ceiling['value'])}, set by {ceiling['limited_by']}.")
    if provisional:
        parts.append("Provisional: more answers are needed before this profile is settled.")
    parts.append(cfg.METHOD_STATUS)
    return " ".join(parts)
