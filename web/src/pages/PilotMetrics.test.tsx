import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PilotMetrics as Metrics } from "../lib/types";

const metrics: Metrics = {
  weeks: 12, feature_days: 30,
  totals: { firms: 1, active_advisers: 1, pending_advisers: 0, clients: 0, questions: 0 },
  weekly_active: [], features: [],
  funnel: { signed_up: 2, registered: 1, approved: 1, added_client: 0, ran_analysis: 0 },
  avg_approval_hours: null, pending: [],
  review_presets: { checks: [], reject_reasons: [] },
  reviews: [
    { reviewed_at: "2026-11-01T20:53:00Z", adviser_email: "dave@firm.com", adviser_name: "Dave Adams",
      crd: "7654321", decision: "approved", reviewed_by: "ops@galtonks.com", checks: [], reason: null,
      summary: "Approved. BrokerCheck CRD 7654321: name matches.", note: null },
    { reviewed_at: "2026-11-01T20:50:00Z", adviser_email: "old@firm.com", adviser_name: null,
      crd: null, decision: "approved", reviewed_by: "sql:postgres", checks: [], reason: null,
      summary: "Approved in the SQL editor (no checks recorded)", note: null },
  ],
};

vi.mock("../lib/api", () => ({ useApi: () => async () => metrics }));

describe("approval audit trail", () => {
  it("links BrokerCheck: per adviser by CRD, and the site itself", async () => {
    const { default: PilotMetrics } = await import("./PilotMetrics");
    render(<QueryClientProvider client={new QueryClient()}><PilotMetrics /></QueryClientProvider>);
    const perCrd = await screen.findByRole("link", { name: /View CRD 7654321 on BrokerCheck/ });
    expect(perCrd.getAttribute("href")).toBe("https://brokercheck.finra.org/individual/summary/7654321");
    expect(screen.getByRole("link", { name: /Search BrokerCheck/ }).getAttribute("href"))
      .toBe("https://brokercheck.finra.org/");
    expect(screen.getByRole("link", { name: "https://brokercheck.finra.org/" }).getAttribute("target")).toBe("_blank");
  });
});
