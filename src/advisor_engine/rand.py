"""Seeded random draws, bit-for-bit the same as the FIS pilot skeleton's.

The skeleton (`app/engine.js`) draws its cash and tax ranges from `rng` (mulberry32, a
32-bit generator) and `gauss` (Box-Muller with a cached spare). Porting both exactly means
the Python cash range follows the skeleton path for path, so parity is asserted to the
cent rather than to a tolerance.

Mulberry32 is integer arithmetic on 32 bits. JavaScript gets there through `| 0`
(wrap to signed 32 bits), `>>>` (unsigned shift), `Math.imul` (32-bit multiply) and a
final `>>> 0`. Addition, multiplication, XOR and OR give the same low 32 bits whether the
operands are read as signed or unsigned, so this port keeps every value as an unsigned
32-bit int and masks after each operation. The uniform draw is that int divided by 2**32,
the same double JavaScript produces.

`gauss` uses `math.log`, `math.sqrt`, `math.sin` and `math.cos`. V8 and the C library
compute those independently, so a normal draw may differ from the skeleton's in the
last bit; the uniform draws underneath never do. tests/test_rand.py checks both.
"""

from __future__ import annotations

import math
from typing import Callable

_MASK = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """`Math.imul`: the low 32 bits of the product."""
    return (a * b) & _MASK


def rng(seed: int) -> Callable[[], float]:
    """Mulberry32 seeded with `seed` (any integer; JavaScript's `seed | 0` keeps 32 bits).

    Returns a function giving the next uniform draw in [0, 1).
    """
    state = seed & _MASK

    def draw() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & _MASK
        t = _imul(state ^ (state >> 15), 1 | state)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & _MASK) ^ t
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return draw


def gauss(r: Callable[[], float]) -> Callable[[], float]:
    """Standard normal draws from uniform draws `r`, by Box-Muller.

    Each pair of uniforms (u, v) gives two normals: m*cos(2*pi*v) is returned and
    m*sin(2*pi*v) is kept for the next call, where m = sqrt(-2 ln u). A u of exactly 0 is
    redrawn (ln 0 is undefined). The order (cosine first, sine cached) is the skeleton's.
    """
    spare: float | None = None

    def draw() -> float:
        nonlocal spare
        if spare is not None:
            s, spare = spare, None
            return s
        u = 0.0
        while u == 0:
            u = r()
        v = r()
        m = math.sqrt(-2 * math.log(u))
        spare = m * math.sin(2 * math.pi * v)
        return m * math.cos(2 * math.pi * v)

    return draw
