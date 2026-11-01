import { afterEach, describe, expect, it, vi } from "vitest";
import { TAB_IDS } from "../pages/ClientWorkspace";
import { ANALYTICS_TABS, pageType } from "./analytics";

describe("analytics page types never carry client data", () => {
  it("reduces every app address to a page type", () => {
    expect(pageType("/")).toBe("/clients");
    expect(pageType("/clients/7672ec7083bc4d1b941e99a192dde7a5")).toBe("/clients/:client/overview");
    expect(pageType("/clients/7672ec70/", "?p=cf74&tab=stress")).toBe("/clients/:client/stress");
    // Audit-trail links carry ids and question text in the query: none of it survives.
    expect(pageType("/clients/7672ec70", "?tab=assistant&p=cf74&turn=bc19&question=Is%20Mrs%20Smith%20too%20risky"))
      .toBe("/clients/:client/assistant");
    expect(pageType("/clients/7672ec70", "?tab=<script>")).toBe("/clients/:client/other");
    expect(pageType("/clients/7672ec70/extra")).toBe("/other");
    expect(pageType("/anything/else", "?email=a@b.com")).toBe("/other");
  });

  it("knows every workspace tab", () => {
    expect([...ANALYTICS_TABS].sort()).toEqual([...TAB_IDS].sort());
  });
});

describe("page views", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    delete window.plausible;
    document.head.querySelectorAll("script[data-domain]").forEach((s) => s.remove());
  });

  async function load(env: Record<string, string>) {
    for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
    return import("./analytics");
  }

  it("are off without a domain, and with the dev sign-in", async () => {
    let a = await load({ VITE_PLAUSIBLE_DOMAIN: "", VITE_DEV_USER: "" });
    a.trackPageview("/clients");
    expect(window.plausible).toBeUndefined();
    vi.resetModules();
    a = await load({ VITE_PLAUSIBLE_DOMAIN: "app.galtonks.com", VITE_DEV_USER: "alice" });
    expect(a.analyticsEnabled).toBe(false);
  });

  it("send only the page type, once per address, from Plausible's manual script", async () => {
    const a = await load({ VITE_PLAUSIBLE_DOMAIN: "app.galtonks.com", VITE_DEV_USER: "" });
    a.trackPageview("/clients/:client/assistant", "/clients/7672?tab=assistant&question=secret");
    a.trackPageview("/clients/:client/assistant", "/clients/7672?tab=assistant&question=secret");
    a.trackPageview("/clients/:client/assistant", "/clients/9999?tab=assistant");
    const script = document.head.querySelector<HTMLScriptElement>("script[data-domain]")!;
    expect(script.dataset.domain).toBe("app.galtonks.com");
    expect(script.src).toBe("https://plausible.io/js/script.manual.js");
    const calls = window.plausible!.q as unknown[][];
    expect(calls).toEqual([
      ["pageview", { u: `${window.location.origin}/clients/:client/assistant` }],
      ["pageview", { u: `${window.location.origin}/clients/:client/assistant` }],
    ]);
    expect(JSON.stringify(calls)).not.toMatch(/secret|7672|9999/);
  });
});
