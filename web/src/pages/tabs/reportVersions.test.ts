import { describe, expect, it } from "vitest";
import type { ChatMessage, Draft } from "../../lib/types";
import { currentDraftId } from "./AssistantTab";
import { pickReport, reportLabel } from "./ReportTab";
import { savedText } from "./DraftEditor";

const draft = (d: Partial<Draft> & { id: string }): Draft => ({
  portfolio_id: "p", original: "Dear client", approved: false, created_at: "2026-11-01T00:00:00Z", ...d,
});

describe("the draft stays open in Galton AI until the next question", () => {
  const drafted: ChatMessage[] = [
    { role: "user", content: "Draft it", turn_id: "t1" },
    { role: "assistant", content: "Dear client", turn_id: "t1", draft_id: "d1" },
  ];
  it("shows the draft the conversation ends on", () => {
    expect(currentDraftId(drafted)).toBe("d1");
  });
  it("closes it once another question is answered", () => {
    expect(currentDraftId([...drafted,
      { role: "user", content: "Why?", turn_id: "t2" },
      { role: "assistant", content: "Because.", turn_id: "t2" }])).toBeNull();
    expect(currentDraftId(undefined)).toBeNull();
  });
});

describe("client report versions", () => {
  // Newest first, as the API lists them.
  const all = [
    draft({ id: "d3" }),
    draft({ id: "d2", approved: true, approved_text: "Edited", approved_at: "2026-11-01T10:00:00Z" }),
    draft({ id: "d1", approved: true, approved_text: "First", approved_at: "2026-10-30T10:00:00Z" }),
  ];
  it("opens the requested report, else the newest approved one", () => {
    expect(pickReport(all, "d1")?.id).toBe("d1");
    expect(pickReport(all, null)?.id).toBe("d2");
    expect(pickReport(all, "gone")?.id).toBe("d2");
    expect(pickReport([all[0]], null)?.id).toBe("d3");
    expect(pickReport([], null)).toBeUndefined();
  });
  it("numbers reports oldest first and says which are approved", () => {
    expect(reportLabel(all[2], all)).toMatch(/^Report 1 · .* · approved$/);
    expect(reportLabel(all[0], all)).toMatch(/^Report 3 · .* · not approved yet$/);
  });
  it("prints the approved text, not the model's original", () => {
    expect(savedText(all[1])).toBe("Edited");
    expect(savedText(all[0])).toBe("Dear client");
  });
});
