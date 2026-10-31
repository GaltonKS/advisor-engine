import { describe, expect, it } from "vitest";
import { assetClassLabel, byKind, lossPct, pct, signed } from "./format";

describe("format", () => {
  it("formats fractions as percentages", () => {
    expect(pct(0.2113)).toBe("21.1%");
    expect(lossPct(-0.0961)).toBe("9.6%");
  });
  it("shows the sign of an engine change with a true minus", () => {
    expect(signed(pct(0.012), 0.012)).toBe("+1.2%");
    expect(signed(pct(-0.003), -0.003)).toBe("−0.3%");
    expect(signed(pct(0), 0)).toBe("0.0%");
  });
  it("names asset classes", () => {
    expect(assetClassLabel("fixed_income")).toBe("Bonds");
    expect(assetClassLabel("unknown_label")).toBe("unknown_label");
  });
  it("formats comparison values by kind", () => {
    expect(byKind("loss", 0.093)).toBe("9.3%");
    expect(byKind("count", 12)).toBe("12");
    expect(byKind("number", 1.29)).toBe("1.3");
  });
});
