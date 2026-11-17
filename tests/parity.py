"""Comparing Python engine output with what the FIS skeleton returned (fixtures under
tests/fixtures/skeleton/). Shared by the parity tests of each step of the port."""

from __future__ import annotations

import json
import re
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "skeleton"
EXAMPLES = ["barbershop", "home-services", "home-services-tax"]
SKELETON_SHA = "cd82452c5ebbdbe4bdc10454ba2ff163b1816d4d3e657da3f8c8932e68a869d5"


def load(ex: str) -> tuple[dict, dict]:
    """(brain, data) for a worked example, fresh copies every call."""
    return (json.loads((FIX / ex / "brain.json").read_text()), json.loads((FIX / ex / "data.json").read_text()))


def fixture(ex: str, name: str) -> dict:
    out = json.loads((FIX / ex / f"{name}.json").read_text())
    assert out["engine_sha256"] == SKELETON_SHA, "fixture from a different skeleton version"
    return out


def variant(data: dict, run: str) -> dict:
    """The snapshot for one of the five runs, cut the same way the capture scripts cut it."""
    if run == "no_customers_ar_ap":
        for k in ("customers", "ar", "ap"):
            data.pop(k, None)
    elif run == "no_books":
        data["pnl"] = {"months": [], "lines": []}
    elif run == "weeks_30":
        n = len(data["sales"]["weeks"])
        data["sales"]["weeks"] = data["sales"]["weeks"][-30:]
        data["sales"]["rows"] = [[r[0] - (n - 30)] + r[1:] for r in data["sales"]["rows"] if r[0] >= n - 30]
    return data


RUNS = ["base", "corrected", "no_customers_ar_ap", "no_books", "weeks_30"]


# Keys Python adds on purpose. Any other key the skeleton lacks is an error, so a renamed or
# stray field cannot slip through.
EXTRA = frozenset({"tie", "method_version", "method_status"})


CENT = 0.005


def _cent_figure(path: str, cents: tuple[str, ...]) -> bool:
    """A simulated percentile: a cash range's week 1 or later (week 0 is today's cash, which is
    exact), or a tax range's percentile for a year."""
    m = re.search(r"\.(\w+)\[(\d+)\]$", path)
    if m:
        return m.group(1) in cents and int(m.group(2)) > 0
    # the tax range: one simulated percentile per year (range.cur.taxable.p50, ...)
    m = re.search(r"\.range\.\w+\.\w+\.(\w+)$", path)
    return bool(m and m.group(1) in cents)


def same(py, js, path: str = "", loose: tuple[str, ...] = (), cents: tuple[str, ...] = (),
         extra: frozenset = EXTRA) -> None:
    """Assert `py` matches the skeleton's `js`, exactly.

    Every key the skeleton has must be present and match; Python may add only the keys in
    `extra` (EXTRA unless a test widens it for its own figures). Numbers must be equal, except at paths ending in one of `loose`
    (figures that go through a log, exp, sin or cos, which V8 and the C library may round
    differently in the last bit): those agree to 1e-12 of their size. A percentile of a
    simulated week (last key in `cents`, week 1 or later) agrees to the cent:
    their draws pass through log/sin/cos and exp, and 2,000 paths add up the last bits. A
    boolean must be a
    boolean (1 is not True). Python's NaN arrives here as None, like JavaScript's JSON null.
    """
    if isinstance(js, dict):
        assert isinstance(py, dict), f"{path}: expected an object, got {py!r:.80}"
        stray = set(py) - set(js) - extra
        assert not stray, f"{path}: keys the skeleton does not have: {sorted(stray)}"
        for k, v in js.items():
            assert k in py, f"{path}.{k}: missing"
            same(py[k], v, f"{path}.{k}", loose, cents, extra)
    elif isinstance(js, list):
        assert isinstance(py, list) and len(py) == len(js), \
            f"{path}: length {len(py) if isinstance(py, list) else py!r} vs {len(js)}"
        for i, (a, b) in enumerate(zip(py, js)):
            same(a, b, f"{path}[{i}]", loose, cents, extra)
    elif isinstance(js, bool) or isinstance(py, bool):
        assert py is js, f"{path}: {py!r} vs {js!r}"
    elif isinstance(js, (int, float)):
        assert isinstance(py, (int, float)), f"{path}: {py!r} vs {js}"
        if cents and _cent_figure(path, cents):
            tol = CENT
        elif loose and path.endswith(loose):
            tol = 1e-12 * max(1, abs(js))
        else:
            tol = 0
        assert abs(py - js) <= tol, f"{path}: {py!r} vs {js!r}"
    else:
        assert py == js, f"{path}: {py!r} vs {js!r}"
