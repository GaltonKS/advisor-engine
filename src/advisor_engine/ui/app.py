"""Local demo UI: `streamlit run src/advisor_engine/ui/app.py`.

Every figure shown comes from the engine's output (the same `get_risk_profile` tool
the assistant uses, so its path rules and audit log apply). The UI only lays the
numbers out; it computes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

import altair as alt
import pandas as pd
import streamlit as st

from advisor_engine import __version__
from advisor_engine.assistant._paths import DATA_DIR, DEFAULT_HOLDINGS, REPO_ROOT
from advisor_engine.assistant.audit import append_audit
from advisor_engine.assistant.chat import DEFAULT_MODEL, llm_mode, run_turn
from advisor_engine.assistant.mock_client import MockClient
from advisor_engine.assistant.numcheck import verify
from advisor_engine.assistant.tools import execute_tool
from advisor_engine.holdings import load_holdings
from advisor_engine.ui import theme

SAMPLE_DIR = DATA_DIR / "sample"
UPLOAD_DIR = DATA_DIR / "uploads"

# Objectives are short descriptions that shape the system prompt. Disclaimer wording
# is the firm's to supply, so only "Custom" has a place to type one.
PROFILES = {
    "Retirement": "retirement income and capital preservation",
    "Growth": "long-term capital growth",
    "Asset transfer": "passing assets to the next generation",
    "REIT/PE": "real estate and private equity holdings",
    "Custom": "",
}
TONES = {
    "Plain and concise": "plain and concise",
    "Detailed": "detailed and thorough",
    "Warm and client-friendly": "warm and client-friendly",
}
DRAFT_REQUEST = "Please write a client-ready explanation of this portfolio's risk, for my review."
# One-click follow-ups under the chat (label -> question sent to the assistant).
FOLLOW_UPS = {
    "Reasoning for the top position": "Why is the largest risk contributor a concern?",
    "Stress test a severe period": "What is the stress loss in a severe period?",
    "Sector exposure": "What is my sector exposure?",
}
# Plain names for the engine tools, shown as "Used" chips on each reply.
TOOL_LABELS = {"get_risk_profile": "Risk profile"}
FOOTER = ("Decision support for registered advisers. Not investment advice. Every figure on "
          "this screen is computed by the engine; returns are synthetic and illustrative.")

st.set_page_config(page_title="KVS · Advisor Engine", layout="wide")
theme.inject()


# --- engine access ---------------------------------------------------------------------

def rel(p) -> str:
    return str(p.relative_to(REPO_ROOT))


def portfolio_files(upload_dir) -> list[str]:
    """Sample portfolios plus this session's own uploads, never other visitors' files."""
    files = sorted(SAMPLE_DIR.glob("*.csv")) + (
        sorted(upload_dir.glob("*.csv")) if upload_dir.is_dir() else []
    )
    return [rel(p) for p in files]


@st.cache_data(show_spinner=False)
def engine_profile(path: str, mtime: float) -> tuple[str, bool, str | None]:
    """The tool's own output for `path` (JSON text), whether it failed, the file hash."""
    res = execute_tool("get_risk_profile", {"holdings_path": path})
    return res.text, res.is_error, res.holdings_sha256


def save_upload(upload, upload_dir) -> tuple[str | None, str | None]:
    """Save a CSV to this session's upload folder if load_holdings accepts it.

    Returns (path, error).
    """
    name = re.sub(r"[^\w.-]", "_", upload.name)
    if not name.lower().endswith(".csv"):
        return None, "The file must be a .csv."
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / name
    existed = target.exists()
    backup = target.read_bytes() if existed else None
    target.write_bytes(upload.getvalue())
    try:
        load_holdings(str(target))
    except Exception as e:  # any load failure means the file is not usable
        if existed:
            target.write_bytes(backup)
        else:
            target.unlink(missing_ok=True)
        return None, f"{upload.name} was rejected: {e}"
    return rel(target), None


# --- display helpers (formatting only) ----------------------------------------------------

def pct(x: float) -> str:
    return f"{x:.1%}"


def badge(msg: dict) -> None:
    if msg["blocked"]:
        st.error(f"**✗ Withheld.** {msg['content']}")
    else:
        n = msg["verification"].get("checked", 0)
        st.markdown(f":green[**✓ Verified**] · {n} figures matched engine output")


def how_checked(msg: dict) -> None:
    with st.expander("How this was checked"):
        st.caption(
            "Every number in the reply is compared with the engine output below. "
            "A reply with a figure the engine did not produce is withheld."
        )
        st.markdown("**Verification result**")
        st.json(msg["verification"], expanded=True)
        for call in msg["tool_calls"]:
            st.markdown(f"**Engine output** (`{call['name']}`)")
            try:
                st.json(json.loads(call["output"]), expanded=False)
            except ValueError:
                st.code(call["output"])


def source_lines(tool_calls: list[dict]) -> list[str]:
    """One provenance line per successful tool call, read from that call's own output."""
    lines = []
    for call in tool_calls:
        if call.get("is_error"):
            continue
        try:
            out = json.loads(call["output"])
        except ValueError:
            continue
        sha = (call.get("holdings_sha256") or "")[:8]
        lines.append(
            f"{TOOL_LABELS.get(call['name'], call['name'])} · {out.get('n_periods')} "
            f"{out.get('drawdown', {}).get('period_label', '')} {out.get('returns_source')} returns"
            f" · engine v{out.get('engine_version', __version__)}"
            + (f" · holdings {sha}" if sha else "")
        )
    return lines


def render_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
            return
        used = list(dict.fromkeys(TOOL_LABELS.get(c["name"], c["name"]) for c in msg["tool_calls"]))
        if used:
            theme.html(theme.chips("Used", used))
        if not msg["blocked"]:
            st.markdown(msg["content"])
        badge(msg)
        if not msg["blocked"] and (lines := source_lines(msg["tool_calls"])):
            theme.html(theme.sources(lines))
        how_checked(msg)


# --- state -------------------------------------------------------------------------------

ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("history", [])
ss.setdefault("draft", None)
ss.setdefault("viewed", set())
ss.setdefault("handled_upload", None)
# Uploads go to a folder named by a random per-session id, so on a shared deployment
# one visitor's files are never listed for another. The id is unguessable, which also
# keeps the assistant (whose path guard allows all of data/) from being pointed at them.
ss.setdefault("session_dir", UPLOAD_DIR / uuid.uuid4().hex)

try:
    mode = llm_mode()
except ValueError as e:
    st.error(str(e))
    st.stop()
is_mock = mode == "mock"


# --- sidebar ---------------------------------------------------------------------------------

with st.sidebar:
    theme.html(theme.brand())
    st.header("Portfolio")
    picker = st.container()
    with st.expander("Upload a holdings CSV"):
        st.caption("Columns: ticker, shares, price, sector. Visible only in this browser session.")
        upload = st.file_uploader("CSV file", type="csv", label_visibility="collapsed")
        if upload is not None:
            token = (upload.name, hashlib.sha256(upload.getvalue()).hexdigest())
            if token != ss.handled_upload:
                ss.handled_upload = token
                saved, err = save_upload(upload, ss.session_dir)
                if err:
                    st.error(err)
                else:
                    ss.portfolio = saved
                    st.success(f"Loaded {upload.name}.")
    options = portfolio_files(ss.session_dir)
    default = rel(DEFAULT_HOLDINGS)
    if ss.get("portfolio") not in options:
        ss.portfolio = default if default in options else options[0]
    portfolio = picker.selectbox("Holdings file", options, key="portfolio")

    st.header("Firm profile")
    profile_name = st.selectbox("Client objective", list(PROFILES))
    objective = PROFILES[profile_name]
    disclaimer = ""
    if profile_name == "Custom":
        objective = st.text_input("Objective", placeholder="e.g. income with low turnover")
        disclaimer = st.text_area("Disclaimer (optional)", help="Appended verbatim to verified replies.")
    tone = TONES[st.selectbox("Tone", list(TONES))]
    firm_profile = {
        "objective": objective.strip(),
        "tone": tone,
        **({"disclaimer": disclaimer.strip()} if disclaimer.strip() else {}),
    }

    st.header("Guardrail demo")
    force_bad = st.checkbox(
        "Simulate invented number",
        disabled=not is_mock,
        help="The mock assistant adds a figure the engine never produced, so you can watch "
        "the number check withhold the reply." + ("" if is_mock else " Mock mode only."),
    )

if ss.get("last_portfolio") != portfolio:  # old answers belong to the old portfolio
    ss.messages, ss.history, ss.draft = [], [], None
    ss.last_portfolio = portfolio


# --- engine data for the panels ------------------------------------------------------------------

abs_path = str(REPO_ROOT / portfolio)
tool_text, tool_failed, sha = engine_profile(abs_path, (REPO_ROOT / portfolio).stat().st_mtime)
if tool_failed:
    st.error(tool_text)
    st.stop()
data = json.loads(tool_text)
ss.setdefault("logged", 0)  # audit lines written by this browser session
if (portfolio, sha) not in ss.viewed:  # one audit entry per portfolio view
    ss.viewed.add((portfolio, sha))
    append_audit({
        "event": "ui_view", "model": None, "holdings_sha256": sha, "user_message": None,
        "tool_calls": [{"name": "get_risk_profile", "input": {"holdings_path": abs_path},
                        "output": tool_text, "is_error": False, "holdings_sha256": sha}],
        "reply": None, "verification": None, "blocked": False,
    })
    ss.logged += 1
conc = data["concentration"]
dd = data["drawdown"]
label = dd["period_label"]


# --- header ----------------------------------------------------------------------------------

title = (REPO_ROOT / portfolio).stem.replace("_", " ").replace("-", " ").strip().capitalize()
subtitle = " · ".join(x for x in [objective.capitalize() if objective else "", f"{conc['n_positions']} holdings",
                                   f"{tone.capitalize()} tone"] if x)
theme.html(theme.header("Client analysis · illustrative sample data", title, subtitle))
st.write("")
if is_mock:
    st.info("**MOCK** · no API calls, replies are scripted · **Synthetic data, illustrative only**")
else:
    st.warning("**LIVE** · questions are sent to the Anthropic API · **Synthetic data, illustrative only**")

c1, c2, c3 = st.columns(3)
with c1:
    theme.html(theme.card(
        "Effective independent bets", f"{conc['effective_bets']:.1f}",
        f"across {conc['n_positions']} holdings: how many separate bets the portfolio behaves like."))
with c2:
    theme.html(theme.card(
        "Largest single source of risk", pct(conc["top_component_share"]),
        "of total risk comes from one common factor shared by the holdings."))
with c3:
    theme.html(theme.card(
        "1-in-100 stress loss", pct(abs(dd["stress_loss_1_in_100"])),
        f"estimated loss over one {label} period in a severe scenario. Not a forecast."))
st.write("")


# --- chat actions (handled before drawing so the new messages render) ----------------------------------

def ask(text: str, is_draft: bool = False) -> None:
    client = MockClient(force_bad_number=force_bad) if is_mock else None
    ss.messages.append({"role": "user", "content": text})
    try:
        with st.spinner("Checking with the engine..."):
            result = run_turn(text, ss.history, client, firm_profile=firm_profile,
                              default_holdings=abs_path)
    except Exception as e:  # missing package, API error, ...
        ss.messages.append({"role": "assistant", "blocked": True, "content": f"Error: {e}",
                            "verification": {}, "tool_calls": []})
        return
    ss.logged += 1
    ss.history = result.history
    ss.messages.append({
        "role": "assistant", "content": result.reply, "blocked": result.blocked,
        "verification": result.verification, "tool_calls": result.tool_calls,
    })
    if is_draft and not result.blocked:
        ss.draft = {"original": result.reply, "tool_calls": result.tool_calls, "approved": False}
        ss.draft_text = result.reply


# --- layout ----------------------------------------------------------------------------------------

chat_col, panel_col = st.columns([5, 4], gap="large")

with chat_col:
    st.subheader("Ask about the portfolio")
    history_box = st.container(height=480, border=True)
    with history_box:
        if not ss.messages:
            st.caption("Try: “How risky is this portfolio?”, “Why is BBB a concern?”, "
                       "“What is my sector exposure?”, “What could I lose in a bad month?”")
        for m in ss.messages:
            render_message(m)

    prompt = st.chat_input("Describe the client or ask a follow-up")
    if prompt:
        ask(prompt)
        st.rerun()
    buttons = [c for _ in range(2) for c in st.columns(2)]  # 2 x 2 grid
    if buttons[0].button("Draft client explanation", type="primary", use_container_width=True):
        ask(DRAFT_REQUEST, is_draft=True)
        st.rerun()
    for col, (text, question) in zip(buttons[1:], FOLLOW_UPS.items()):
        if col.button(text, use_container_width=True):
            ask(question)
            st.rerun()
    st.caption("These are options to weigh, not a recommendation. Which to use is your call.")

    if ss.draft:
        theme.html(theme.section("Draft explanation for the client", "Editable · yours to approve"))
        st.caption("Edit as needed. Approving records it in the audit log. Nothing is sent anywhere.")
        text = st.text_area("Draft", key="draft_text", height=300, label_visibility="collapsed")
        if ss.draft["approved"]:
            st.success("Approved and logged. Nothing was sent.")
        elif st.button("Approve", type="primary"):
            check = verify(text, [c["output"] for c in ss.draft["tool_calls"]])
            if not check["ok"]:
                st.error("Not approved: these figures are not from the engine: "
                         + ", ".join(check["unmatched"]))
            else:
                append_audit({
                    "event": "draft_approved", "model": None, "holdings_sha256": sha,
                    "user_message": DRAFT_REQUEST, "tool_calls": ss.draft["tool_calls"],
                    "reply": ss.draft["original"], "approved_text": text,
                    "edited": text != ss.draft["original"], "verification": check,
                    "blocked": False,
                })
                ss.logged += 1
                ss.draft["approved"] = True
                st.rerun()

with panel_col:
    t_src, t_over, t_pos, t_sec, t_dd = st.tabs(
        ["Risk sources", "Overview", "Positions", "Sectors", "Drawdown"])
    positions = pd.DataFrame(data["positions"])

    with t_src:
        shares = conc["component_shares"]
        theme.html(theme.bars(
            "Where the risk comes from",
            "Share of the portfolio's risk by independent source, largest factor first.",
            [("Largest common factor" if i == 0 else f"Factor {i + 1}", pct(x), x)
             for i, x in enumerate(shares)],
            f"Method: principal component analysis on a Ledoit-Wolf shrunk covariance of "
            f"{data['n_periods']} {label} returns ({data['returns_source']}). Factors are "
            "statistical, not named markets or styles. Every figure is computed by the engine, "
            "not written by the language model.",
        ))
    with t_over:
        a, b = st.columns(2)
        a.metric("Positions", conc["n_positions"])
        b.metric("Independent bets", f"{conc['effective_bets']:.1f}",
                 help="How many truly separate bets the portfolio behaves like, given how "
                      "closely the holdings move together.")
        c, d = st.columns(2)
        c.metric("After removing the market", f"{conc['market_adjusted']['residual_effective_bets']:.1f}",
                 help="Independent bets left once the overall market move is taken out.")
        sl = conc["sector_level"]
        if "sector_effective_bets" in sl:
            d.metric("At sector level", f"{sl['sector_effective_bets']:.1f}",
                     help=f"Independent bets across {sl['n_sectors']} sectors.")
        else:
            d.metric("At sector level", "Not available", help=sl.get("withheld"))
        if "market_share_of_risk" in conc["market_adjusted"]:
            st.metric("Share of risk from the market", pct(conc["market_adjusted"]["market_share_of_risk"]))
        if conc["market_adjusted"]["market_proxy"] == "equal_weight_universe":
            st.caption("The market is approximated by an equal-weighted mix of these holdings, "
                       "so treat the market-adjusted figure with care.")
        st.caption(f"Returns: {data['returns_source']} · {data['n_periods']} {data['drawdown']['period_label']} periods")
        st.markdown("**Largest positions by weight**")
        top = pd.DataFrame(data["largest_positions"]).rename(
            columns={"ticker": "Ticker", "weight": "Weight", "sector": "Sector"})
        st.dataframe(top[["Ticker", "Sector", "Weight"]].style.format({"Weight": "{:.1%}"}),
                     hide_index=True, use_container_width=True)

    with t_pos:
        table = positions.rename(columns={
            "ticker": "Ticker", "sector": "Sector", "weight": "Weight", "risk_share": "Share of risk",
            "risk_to_weight": "Risk vs size", "avg_corr_to_rest": "Correlation to rest"})
        st.dataframe(
            table[["Ticker", "Sector", "Weight", "Share of risk", "Risk vs size", "Correlation to rest"]]
            .style.format({"Weight": "{:.1%}", "Share of risk": "{:.1%}", "Risk vs size": "{:.2f}x",
                           "Correlation to rest": "{:.2f}"}),
            hide_index=True, use_container_width=True)
        st.caption("Risk vs size above 1.00x means a position adds more risk than its weight suggests. "
                   "Click a column header to sort.")
        long = positions.melt(id_vars="ticker", value_vars=["weight", "risk_share"],
                              var_name="measure", value_name="share")
        long["measure"] = long["measure"].map({"weight": "Weight", "risk_share": "Share of risk"})
        st.altair_chart(
            alt.Chart(long).mark_bar().encode(
                x=alt.X("ticker:N", sort=list(positions["ticker"]), title=None),
                xOffset="measure:N",
                y=alt.Y("share:Q", axis=alt.Axis(format="%"), title=None),
                color=alt.Color("measure:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("ticker:N", title="Ticker"), alt.Tooltip("measure:N", title="Measure"),
                         alt.Tooltip("share:Q", title="Value", format=".1%")],
            ).properties(height=300),
            use_container_width=True)

    with t_sec:
        sectors = pd.DataFrame({"sector": list(data["sector_exposure"]),
                                "weight": list(data["sector_exposure"].values())})
        st.altair_chart(
            alt.Chart(sectors).mark_bar().encode(
                y=alt.Y("sector:N", sort=list(sectors["sector"]), title=None),
                x=alt.X("weight:Q", axis=alt.Axis(format="%"), title="Share of portfolio"),
                tooltip=[alt.Tooltip("sector:N", title="Sector"),
                         alt.Tooltip("weight:Q", title="Weight", format=".1%")],
            ).properties(height=max(160, 34 * len(sectors))),
            use_container_width=True)
        sl = conc["sector_level"]
        if "sector_effective_bets" in sl:
            st.caption(f"Across {sl['n_sectors']} sectors the portfolio behaves like "
                       f"{sl['sector_effective_bets']:.1f} independent sector bets.")
        cov = data.get("sector_coverage") or {}
        if cov.get("n_unclassified"):
            st.caption(f"{pct(cov['unclassified_weight'])} of the portfolio "
                       f"({', '.join(cov['unclassified_tickers'])}) has no sector, so sector-level "
                       "figures are not computed. Add a sector column to the CSV to include them.")

    with t_dd:
        a, b = st.columns(2)
        a.metric("Worst historical drop", pct(abs(dd["historical_max_drawdown"])),
                 help="Largest peak-to-trough loss in the return history analysed.")
        b.metric("1-in-100 stress loss", pct(abs(dd["stress_loss_1_in_100"])),
                 help=f"Estimated loss over one {label} period in a severe (1-in-100) scenario.")
        st.metric(f"Typical {label} volatility", pct(dd["period_volatility"]))
        st.caption(f"All figures are on a {label} basis. They describe history and a stress "
                   "scenario; they are not forecasts.")

    st.write("")
    draft_state = ("Approved" if ss.draft and ss.draft["approved"]
                   else "Awaiting your approval" if ss.draft else "No draft yet")
    theme.html(theme.audit_panel(
        [
            ("Engine version", f"v{data.get('engine_version', __version__)}"),
            ("Returns", f"{data['n_periods']} {label} · {data['returns_source']}"),
            ("Holdings file", f"{portfolio.rsplit('/', 1)[-1]} · {sha[:8]}"),
            ("Assistant", "mock (scripted)" if is_mock
             else os.environ.get("ADVISOR_MODEL", DEFAULT_MODEL)),
            ("Logged this session", f"{ss.logged} entries"),
            ("Client draft", draft_state),
        ],
        "Every output is logged with its inputs, so you can show how an explanation was reached.",
    ))

theme.html(theme.footer(FOOTER))
