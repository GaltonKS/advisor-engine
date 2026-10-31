import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown, parseBlocks } from "./markdown";

afterEach(cleanup);

// A live reply as the assistant wrote it (from the advisor's screenshot).
const REPLY = `Risk follows weight closely in this portfolio, and Technology is the dominant sector on both measures. Technology is 44.3% of the portfolio and 45.2% of its risk (chart below).

**Where the risk sits**
- **Technology:** 44.3% of weight and 45.2% of risk. It adds about as much risk as its size suggests.
- **Consumer Discretionary:** 13.1% of weight and 14.6% of risk. It adds somewhat more risk than its size suggests (AMZN and TSLA are in this group).
- **Energy and Utilities:** small groups at 1.8% and 1.5% of weight, with 2.2% and 1.7% of risk.

**What it means for a growth-tilted client**
- Across all 24 positions, it behaves like about 1.3 independent bets. One common factor accounts for about 94.9% of the risk.
- Its monthly volatility is 4.2%. Its worst historical drop is 21.4% on a monthly basis.`;

/** The reply with only Markdown syntax removed: what the reader should see, word for word. */
const plain = (md: string) => md
  .replace(/\*\*/g, "")
  .replace(/^\s*[-*•]\s+/gm, "")
  .replace(/^\s*#{1,4}\s+/gm, "")
  .replace(/\s+/g, " ").trim();

describe("Markdown", () => {
  it("renders headings, bullets and bold labels instead of raw symbols", () => {
    const { container } = render(<Markdown text={REPLY} />);
    expect(container.textContent).not.toContain("**");
    expect([...container.querySelectorAll("h4")].map((h) => h.textContent))
      .toEqual(["Where the risk sits", "What it means for a growth-tilted client"]);
    expect(container.querySelectorAll("li").length).toBe(5);
    expect(container.querySelector("li strong")?.textContent).toBe("Technology:");
  });

  it("keeps every word and figure of the reply", () => {
    const { container } = render(<Markdown text={REPLY} />);
    const shown = (container.textContent ?? "").replace(/\s+/g, " ").trim();
    // Block boundaries add no characters, so compare with spaces removed too.
    expect(shown.replace(/\s/g, "")).toBe(plain(REPLY).replace(/\s/g, ""));
    for (const fig of REPLY.match(/\d+(\.\d+)?%?/g) ?? []) expect(shown).toContain(fig);
  });

  it("never renders HTML from the text", () => {
    const { container } = render(<Markdown text={'**Note** <script>alert(1)</script> <img src=x onerror=alert(1)>'} />);
    expect(container.querySelector("script, img")).toBeNull();
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("renders numbered lists, simple tables and wrapped bullet lines", () => {
    const blocks = parseBlocks("1. First\n2. Second\n\n| Sector | Weight |\n|---|---|\n| Energy | 3.8% |\n\n- a long point\n  that wraps\n- next");
    expect(blocks.map((b) => b.kind)).toEqual(["ol", "table", "ul"]);
    expect(blocks[2]).toEqual({ kind: "ul", items: ["a long point that wraps", "next"] });
    const { container } = render(<Markdown text={"| Sector | Weight |\n|---|---|\n| Energy | 3.8% |"} />);
    expect(container.querySelector("td")?.textContent).toBe("Energy");
  });

  it("leaves stray asterisks and minus signs in figures alone", () => {
    const { container } = render(<Markdown text={"A change of -0.58% and a 2*3 grid, not a list."} />);
    expect(container.textContent).toBe("A change of -0.58% and a 2*3 grid, not a list.");
  });
});
