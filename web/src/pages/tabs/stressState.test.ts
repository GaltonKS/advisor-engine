import { describe, expect, it } from "vitest";
import type { AuditRow, StressResult, StressRun } from "../../lib/types";
import { activityLink, describe as describeRow, describeScenario } from "./AuditTab";
import { EMPTY_STRESS, stateFromRun } from "./StressTab";

const result = { scenario: { id: "custom", label: "Custom scenario", sectors: {}, default: -0.05 } } as unknown as StressResult;
const row = (r: Partial<AuditRow>): AuditRow => ({
  timestamp: "2026-11-01T00:00:00Z", event: null, engine_version: "0.7.0", model: null,
  adviser_id: null, portfolio_id: "p", user_message: null, blocked: null, verified: null, ...r,
});

describe("stress scenario state", () => {
  it("refills the fields from a custom run, as percentages", () => {
    const run: StressRun = {
      run_id: "r1", timestamp: "2026-11-01T00:00:00Z", result,
      inputs: { sector_shocks: { Technology: -0.3 }, asset_class_shocks: { fixed_income: 0.025 },
                default_shock: -0.05, description: "Chip supply shock", suggested: null },
    };
    expect(stateFromRun(run)).toEqual({
      custom: { Technology: "-30" }, classCustom: { fixed_income: "2.5" }, other: "-5",
      description: "Chip supply shock", open: true, suggestion: null, result,
    });
  });
  it("keeps the fields as they were for a preset run", () => {
    const base = { ...EMPTY_STRESS, description: "kept" };
    const s = stateFromRun({ run_id: "r2", timestamp: "", result, inputs: { scenario_id: "broad_selloff" } }, base);
    expect(s.description).toBe("kept");
    expect(s.result).toBe(result);
  });
});

describe("stress runs in the audit trail", () => {
  it("names a preset", () => {
    expect(describeScenario({ id: "broad_selloff", label: "Broad sell-off", source: "preset" }))
      .toBe("Stress test: Broad sell-off (preset)");
  });
  it("spells out a custom scenario's description and shocks", () => {
    expect(describeScenario({
      id: "custom", label: "Custom scenario", source: "adviser", description: "Chip supply shock",
      sector_shocks: { Technology: -0.3 }, asset_class_shocks: { fixed_income: 0.025 }, default_shock: -0.05,
    })).toBe("Stress test (custom): “Chip supply shock” · Technology −30%, Bonds +2.5%, all other −5%");
  });
  it("says when the AI suggested the shocks", () => {
    expect(describeScenario({ id: "custom", label: "Taiwan blockade", source: "ai_suggested_edited",
                              sector_shocks: {}, asset_class_shocks: {}, default_shock: 0 }))
      .toBe("Stress test (custom, AI-suggested, edited): Taiwan blockade · all other 0%");
  });
  it("falls back for rows without a summary, and links runs back to the Stress test tab", () => {
    expect(describeRow(row({ event: "stress" }))).toBe("Stress scenario run");
    expect(activityLink(row({ event: "stress", run_id: "r1" }))).toMatchObject({ tab: "stress", params: { run: "r1" } });
    expect(activityLink(row({ event: "stress" }))).toBeNull();
    expect(activityLink(row({ event: "turn", turn_id: "t1" }))).toMatchObject({ tab: "assistant", params: { turn: "t1" } });
  });
});
