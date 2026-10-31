"""KVS look for the Streamlit demo: one stylesheet and small HTML builders.

Formatting only. Callers pass strings already formatted from engine output; nothing
here computes a figure. Every value is HTML-escaped, since tickers, sectors and file
names can come from an uploaded CSV.
"""

from __future__ import annotations

from html import escape

import streamlit as st

INK = "#1F1B16"
GOLD = "#B8952F"
GOLD_DARK = "#8A6D1F"
PAPER = "#F5EFE4"
CARD = "#FDFAF3"
RULE = "#E6DCC8"
TRACK = "#EFE5CF"

SERIF = 'Georgia, "Iowan Old Style", "Times New Roman", serif'
MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'

CSS = f"""
<style>
h1, h2, h3, h4, [data-testid="stHeading"] h1, [data-testid="stHeading"] h2,
[data-testid="stHeading"] h3, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h3, [data-testid="stMarkdownContainer"] h4,
[data-testid="stSidebar"] h2 {{ font-family: {SERIF} !important; font-weight: 500 !important;
  letter-spacing: -0.01em; }}
.kvs-section {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  flex-wrap: wrap; margin-top: 12px; }}
.kvs-section h4 {{ margin: 0; padding: 0; }}
[data-testid="stSidebar"] {{ border-right: 1px solid {RULE}; }}
.kvs-brand {{ display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }}
.kvs-mark {{ border: 1px solid {INK}; font-family: {SERIF}; padding: 2px 4px; font-size: 14px; }}
.kvs-name {{ font-family: {SERIF}; font-size: 16px; line-height: 1.1; color: {INK}; }}
.kvs-eyebrow {{ font-family: {MONO}; font-size: 11px; letter-spacing: 0.12em;
  text-transform: uppercase; color: {GOLD_DARK}; }}
.kvs-sub {{ color: #5C554B; font-size: 15px; margin-top: -6px; }}
.kvs-card {{ background: {CARD}; border: 1px solid {RULE}; border-top: 3px solid {GOLD};
  padding: 16px 20px 14px; height: 100%; }}
.kvs-card .kvs-value {{ font-family: {SERIF}; font-size: 42px; line-height: 1.15;
  color: {INK}; margin: 6px 0 8px; }}
.kvs-card .kvs-unit {{ font-size: 20px; color: #6F675C; }}
.kvs-card .kvs-note {{ font-size: 13px; color: #5C554B; }}
.kvs-panel {{ background: {CARD}; border: 1px solid {RULE}; padding: 18px 20px; }}
.kvs-bar-row {{ margin: 10px 0 12px; font-size: 14px; color: {INK}; }}
.kvs-bar-head {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
.kvs-bar-head span:last-child {{ font-family: {MONO}; font-size: 13px; }}
.kvs-track {{ background: {TRACK}; height: 8px; }}
.kvs-fill {{ background: {GOLD}; height: 8px; }}
.kvs-method {{ font-size: 12px; color: #6F675C; border-top: 1px solid {RULE};
  padding-top: 10px; margin-top: 14px; }}
.kvs-chips {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }}
.kvs-chip {{ background: #F1E6C8; color: {INK}; font-size: 12px; padding: 2px 8px; }}
.kvs-sources {{ font-size: 12px; color: #6F675C; margin-top: 4px; }}
.kvs-sources b {{ color: {GOLD_DARK}; font-weight: 500; font-family: {MONO}; }}
.kvs-audit {{ background: {INK}; color: #EDE6D6; padding: 18px 20px; }}
.kvs-audit h4 {{ color: #FDFAF3; margin: 0 0 10px; font-size: 20px; }}
.kvs-audit .kvs-row {{ display: flex; justify-content: space-between; gap: 12px;
  font-size: 13px; padding: 5px 0; }}
.kvs-audit .kvs-row span:last-child {{ font-family: {MONO}; text-align: right;
  overflow-wrap: anywhere; }}
.kvs-audit .kvs-foot {{ font-size: 11px; color: #B5AC9C; border-top: 1px solid #3A342C;
  margin-top: 10px; padding-top: 10px; }}
.kvs-footer {{ font-size: 12px; color: #6F675C; border-top: 1px solid {RULE};
  margin-top: 28px; padding-top: 10px; }}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def brand() -> str:
    return (
        '<div class="kvs-brand"><span class="kvs-mark">GKS</span><div>'
        '<div class="kvs-name">GaltonKS Knowledge System</div>'
        '<div class="kvs-eyebrow">Registered advisers only</div></div></div>'
    )


def header(eyebrow: str, title: str, subtitle: str) -> str:
    return (
        f'<div class="kvs-eyebrow">{escape(eyebrow)}</div>'
        f"<h1>{escape(title)}</h1>"
        f'<div class="kvs-sub">{escape(subtitle)}</div>'
    )


def card(eyebrow: str, value: str, note: str, unit: str = "") -> str:
    unit_html = f' <span class="kvs-unit">{escape(unit)}</span>' if unit else ""
    return (
        f'<div class="kvs-card"><div class="kvs-eyebrow">{escape(eyebrow)}</div>'
        f'<div class="kvs-value">{escape(value)}{unit_html}</div>'
        f'<div class="kvs-note">{escape(note)}</div></div>'
    )


def bars(title: str, intro: str, rows: list[tuple[str, str, float]], method: str) -> str:
    """`rows` are (label, formatted value, fill fraction 0..1) for a horizontal bar each."""
    body = "".join(
        f'<div class="kvs-bar-row"><div class="kvs-bar-head"><span>{escape(label)}</span>'
        f"<span>{escape(shown)}</span></div>"
        f'<div class="kvs-track"><div class="kvs-fill" '
        f'style="width:{max(0.0, min(1.0, frac)) * 100:.2f}%"></div></div></div>'
        for label, shown, frac in rows
    )
    return (
        f'<div class="kvs-panel"><h3 style="margin:0">{escape(title)}</h3>'
        f'<div class="kvs-note" style="font-size:13px;color:#5C554B">{escape(intro)}</div>'
        f'{body}<div class="kvs-method">{escape(method)}</div></div>'
    )


def chips(label: str, items: list[str]) -> str:
    parts = "".join(f'<span class="kvs-chip">{escape(i)}</span>' for i in items)
    return f'<div class="kvs-chips"><span class="kvs-eyebrow">{escape(label)}</span>{parts}</div>'


def sources(lines: list[str]) -> str:
    rows = "".join(
        f"<div><b>[{i}]</b> {escape(line)}</div>" for i, line in enumerate(lines, start=1)
    )
    return f'<div class="kvs-sources"><div class="kvs-eyebrow">Sources</div>{rows}</div>'


def audit_panel(rows: list[tuple[str, str]], foot: str) -> str:
    body = "".join(
        f'<div class="kvs-row"><span>{escape(k)}</span><span>{escape(v)}</span></div>'
        for k, v in rows
    )
    return (
        f'<div class="kvs-audit"><h4>&#128274; Audit trail</h4>{body}'
        f'<div class="kvs-foot">{escape(foot)}</div></div>'
    )


def section(title: str, tag: str) -> str:
    """A section heading with a small uppercase tag on the right."""
    return (
        f'<div class="kvs-section"><h4>{escape(title)}</h4>'
        f'<span class="kvs-eyebrow">{escape(tag)}</span></div>'
    )


def footer(text: str) -> str:
    return f'<div class="kvs-footer">{escape(text)}</div>'
