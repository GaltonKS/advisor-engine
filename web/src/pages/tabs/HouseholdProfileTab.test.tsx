import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HouseholdProfile, ProfileQuestion, ProfileSnapshot } from "../../lib/types";
import { DimensionBar, ProfilePanel, QuestionCard, SnapshotList, estimateText } from "./HouseholdProfileTab";

afterEach(cleanup);

const est = (mean: number, band: number, low: number, high: number) => ({ mean, band, low, high, n_answers: 2 });
const scale = { min: 1, max: 10 };

// Deliberately inconsistent figures (low/high don't equal mean ∓ band, the household
// isn't the lower partner): the screen must show the engine's numbers, not its own.
const profile: HouseholdProfile = {
  people: [
    { id: "d", name: "Dana", role: "primary", willingness: est(6.2, 1.1, 5.1, 7.3) },
    { id: "m", name: "Marcus", role: "partner", willingness: est(4.0, 1.5, 2.5, 5.5) },
  ],
  household_willingness: { ...est(4.04, 1.5, 2.5, 5.5), person_id: "m" },
  ability: est(5.5, 1.2, 4.3, 6.7),
  need: null,
  ceiling: { value: 3.97, band: 1.5, limited_by: "willingness", person_id: "m" },
  scale,
  provisional: true,
  flags: [{ code: "partner_gap", severity: "warn", message: "Dana and Marcus differ in willingness by 2.2 points.",
            refs: { dimensions: ["willingness"], person_ids: ["d", "m"] } }],
  plain_summary: "Willingness: Dana 6.2 (±1.1), Marcus 4.0 (±1.5). Draft risk profile method (draft-2), pending compliance review.",
  method_version: "draft-2",
  method_status: "Draft risk profile method (draft-2), pending compliance review.",
  inputs_hash: "abcdef1234567890",
  answers: [],
};

describe("ProfilePanel", () => {
  it("shows the engine's figures, a dash for unanswered, the badge, flags and summary", () => {
    render(<ProfilePanel profile={profile} />);
    expect(screen.getByTestId("ceiling").textContent).toContain("4.0");
    expect(screen.getByText("Set by Marcus (± 1.5).")).toBeTruthy();
    expect(screen.getByTestId("bar-Dana · willingness").textContent).toContain("6.2 ± 1.1");
    expect(screen.getByTestId("bar-Household willingness (the lower)").textContent).toContain("4.0 ± 1.5");
    expect(screen.getByTestId("bar-Need").textContent).toContain("—");
    expect(screen.getByTestId("provisional")).toBeTruthy();
    expect(screen.getByTestId("flag-partner_gap").textContent).toContain("2.2 points");
    expect(screen.getByTestId("summary").textContent).toBe(profile.plain_summary);
    expect(screen.getAllByText(/pending compliance review/).length).toBeGreaterThan(0);
  });

  it("has no badge once the engine says the profile is settled, and a dash with no ceiling", () => {
    render(<ProfilePanel profile={{ ...profile, provisional: false, ceiling: null, flags: [] }} />);
    expect(screen.queryByTestId("provisional")).toBeNull();
    expect(screen.getByTestId("ceiling").textContent).toContain("—");
  });
});

describe("DimensionBar", () => {
  it("draws the band from the engine's low and high", () => {
    const { container } = render(<DimensionBar label="Ability" estimate={est(5.5, 1.2, 1, 10)} scale={scale} />);
    const band = container.querySelector(".bg-gold\\/40") as HTMLElement;
    expect(band.style.left).toBe("0%");
    expect(band.style.right).toBe("calc(0%)"); // jsdom simplifies calc(100% - 100%)
  });
  it("formats an estimate, or a dash", () => {
    expect(estimateText(est(6.123, 0.95, 0, 0))).toBe("6.1 ± 0.9");
    expect(estimateText(null)).toBe("—");
  });
});

describe("QuestionCard", () => {
  const choice: ProfileQuestion = {
    id: "w_reaction", dimension: "willingness", kind: "choice", noise_sd: 2,
    prompt: "If the portfolio fell sharply over a year, what would they do?",
    options: [{ key: "sell_all", label: "Sell everything", value: 1 }, { key: "hold", label: "Hold and wait", value: 5.5 }],
  };

  it("sends the option key, shows the chosen one, and can clear it", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard q={choice} answer="hold" onAnswer={onAnswer} busy={false} />);
    expect(screen.getByRole("radio", { name: "Hold and wait" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: "Sell everything" }));
    expect(onAnswer).toHaveBeenCalledWith("sell_all");
    fireEvent.click(screen.getByText("Clear answer"));
    expect(onAnswer).toHaveBeenLastCalledWith(null);
  });

  it("sends loss limits as fractions from two percent sliders", () => {
    const onAnswer = vi.fn();
    const q: ProfileQuestion = { id: "w_loss_limits", dimension: "willingness", kind: "loss_limits",
                                 noise_sd: 1.25, prompt: "Loss limits", min: 0, max: 0.5 };
    render(<QuestionCard q={q} answer={undefined} onAnswer={onAnswer} busy={false} />);
    fireEvent.change(screen.getByLabelText("Fall that would make them uncomfortable"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Fall at which they would act (sell)"), { target: { value: "25" } });
    fireEvent.click(screen.getByText("Save loss limits"));
    expect(onAnswer).toHaveBeenCalledWith({ uncomfortable_pct: 0.15, act_pct: 0.25 });
    expect(screen.queryByText(/\$/)).toBeNull(); // percent only, no dollar translation
  });

  it("takes required return as a percentage and horizon in years", () => {
    const onAnswer = vi.fn();
    const rr: ProfileQuestion = { id: "n_required_return", dimension: "need", kind: "number", noise_sd: 1,
                                  prompt: "What annual return does the plan require?", unit: "fraction", min: 0, max: 0.25 };
    const { unmount } = render(<QuestionCard q={rr} answer={0.058} onAnswer={onAnswer} busy={false} />);
    const input = screen.getByLabelText(rr.prompt) as HTMLInputElement;
    expect(input.value).toBe("5.8");
    fireEvent.change(input, { target: { value: "6" } });
    fireEvent.click(screen.getByText("Save"));
    expect(onAnswer).toHaveBeenCalledWith(0.06);
    unmount();
    const hz: ProfileQuestion = { id: "a_horizon_years", dimension: "ability", kind: "number", noise_sd: 1.5,
                                  prompt: "In how many years?", unit: "years", min: 0, max: 60 };
    render(<QuestionCard q={hz} answer={undefined} onAnswer={onAnswer} busy={false} />);
    fireEvent.change(screen.getByLabelText(hz.prompt), { target: { value: "12" } });
    fireEvent.click(screen.getByText("Save"));
    expect(onAnswer).toHaveBeenLastCalledWith(12);
  });
});

describe("SnapshotList", () => {
  it("lists saved versions newest first with the frozen ceiling", () => {
    const snap = (id: string, value: number | null): ProfileSnapshot => ({
      id, client_id: "c", created_by: "a", method_version: "draft-2", inputs_hash: `${id}0000000000`,
      created_at: "2026-11-06T10:00:00Z",
      result: { ...profile, ceiling: value === null ? null : { value, band: 1, limited_by: "ability" } },
    });
    render(<SnapshotList snapshots={[snap("b", 4.5), snap("a", null)]} />);
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByText(/Ceiling 4\.5/)).toBeTruthy();
    expect(screen.getByText(/Ceiling —/)).toBeTruthy();
  });
  it("says when there are none", () => {
    render(<SnapshotList snapshots={[]} />);
    expect(screen.getByText("No saved versions yet.")).toBeTruthy();
  });
});
