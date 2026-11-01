import { describe, expect, it } from "vitest";
import type { AuditRow, ChatMessage } from "../../lib/types";
import { focusedIndexes } from "./AssistantTab";
import { conversationLink } from "./AuditTab";

const row = (r: Partial<AuditRow>): AuditRow => ({
  timestamp: "2026-11-01T00:00:00Z", event: null, engine_version: "0.6.0", model: null,
  adviser_id: null, portfolio_id: "p", user_message: null, blocked: null, verified: null, ...r,
});

const messages: ChatMessage[] = [
  { role: "user", content: "How risky?" },                       // before turn ids
  { role: "assistant", content: "Quite." },
  { role: "user", content: "How risky?", turn_id: "t1" },
  { role: "assistant", content: "Still quite.", turn_id: "t1" },
  { role: "user", content: "Draft it", turn_id: "t2" },
  { role: "assistant", content: "Dear client", turn_id: "t2", draft_id: "d1" },
];

describe("audit trail links into the conversation", () => {
  it("links turns by id, approvals by draft, older turns by question", () => {
    expect(conversationLink(row({ event: "turn", turn_id: "t1", user_message: "How risky?" }))).toEqual({ turn: "t1" });
    expect(conversationLink(row({ event: "turn", user_message: "How risky?" }))).toEqual({ question: "How risky?" });
    expect(conversationLink(row({ event: "draft_approved", draft_id: "d1" }))).toEqual({ draft: "d1" });
    expect(conversationLink(row({ event: "stress" }))).toBeNull();
  });

  it("marks the question and its reply", () => {
    expect(focusedIndexes(messages, { turn: "t1" })).toEqual([2, 3]);
    expect(focusedIndexes(messages, { draft: "d1" })).toEqual([4, 5]);
    // Text matching only covers messages without turn ids, so it can't pick the wrong one.
    expect(focusedIndexes(messages, { question: "How risky?" })).toEqual([0, 1]);
    expect(focusedIndexes(messages, { turn: "missing" })).toEqual([]);
  });
});
