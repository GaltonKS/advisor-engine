import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RiskByGroupChart, RiskVsSizeChart, WhatIfChart } from "../lib/types";
import { ChartView, RiskSourceBars } from "./charts";

const source = { label: "Risk decomposition", n_periods: 120, period_label: "monthly",
                 returns_source: "synthetic", engine_version: "0.6.0", holdings_sha256: "25b4d477aaaa" };
const base = { subtitle: "s", caveat: "Synthetic returns: illustrative, not a forecast.", source };

const byGroup: RiskByGroupChart = {
  ...base, kind: "risk_by_group", group: "sector", title: "Risk by sector", axis_max: 0.6, note: "",
  rows: [{ label: "Dividend Growth", weight: 0.277, risk_share: 0.483, risk_to_weight: 1.744 },
         { label: "Short Treasuries", weight: 0.071, risk_share: 0.006, risk_to_weight: 0.08 }],
};

afterEach(cleanup);

describe("charts", () => {
  it("draws engine values as given, with the risk ÷ weight column bold above 1", () => {
    render(<ChartView spec={byGroup} />);
    expect(screen.getByText("48.3%")).toBeTruthy();
    expect(screen.getByText("1.74×").getAttribute("font-weight")).toBe("700");
    expect(screen.getByText("0.08×").getAttribute("font-weight")).toBe("400");
    expect(screen.getByText(/Risk decomposition · 120 monthly synthetic returns · engine v0.6.0 · holdings 25b4d477/)).toBeTruthy();
  });

  it("has a table view with the same figures", () => {
    render(<ChartView spec={byGroup} />);
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("Short Treasuries")).toBeTruthy();
  });

  it("shows stress loss with a true minus and whole-number risk sources", () => {
    const spec: WhatIfChart = {
      ...base, kind: "what_if", title: "What-if", option_label: "Without SCHD",
      mix: [{ label: "Current", sources: [{ name: "Largest common factor", share: 0.52 }, { name: "All smaller factors", share: 0.48 }] },
            { label: "Without SCHD", sources: [{ name: "Largest common factor", share: 0.41 }, { name: "All smaller factors", share: 0.59 }] }],
      measures: [{ key: "stress_loss_1_in_100", label: "Stress loss", hint: "h", kind: "loss", current: 0.081, option: 0.061 }],
      added: [], removed: ["SCHD"],
    };
    render(<ChartView spec={spec} />);
    expect(screen.getByText("−8.1%")).toBeTruthy();
    expect(screen.getByText("−6.1%")).toBeTruthy();
    expect(screen.getByText("52%")).toBeTruthy();
  });

  it("labels at most the dots the engine marked", () => {
    const spec: RiskVsSizeChart = {
      ...base, kind: "risk_vs_size", title: "Risk versus size", x_max: 0.2, y_max: 0.4,
      highlight_rule: "Share of risk at least 1.2× weight",
      points: [{ ticker: "SCHD", weight: 0.186, risk_share: 0.355, risk_to_weight: 1.9, highlight: true, label: true },
               { ticker: "CASH", weight: 0.069, risk_share: 0.0001, risk_to_weight: 0.001, highlight: false, label: false }],
    };
    const { container } = render(<ChartView spec={spec} />);
    expect(container.querySelectorAll("circle").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryAllByText("SCHD").length).toBeGreaterThan(0);
    expect([...container.querySelectorAll("text")].some((t) => t.textContent === "CASH")).toBe(false);
  });

  it("Overview risk sources use whole numbers", () => {
    render(<RiskSourceBars sources={[{ name: "Largest common factor", share: 0.523 }]} />);
    expect(screen.getByText("52%")).toBeTruthy();
  });
});
