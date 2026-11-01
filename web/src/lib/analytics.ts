// Plausible page views, and nothing else. Plausible sets no cookies and stores no IP
// addresses (see the Privacy Policy's "Cookies and Analytics" section, which this must
// stay within: page views only, no custom events until the policy says otherwise).
//
// Client data never leaves through analytics. Plausible is never given the browser's
// real address: every page view is reported as a page *type* built here from an allowlist,
// e.g. /clients/:client/assistant. Client ids, portfolio ids and query details (the
// audit-trail links carry question text in `?question=`) are dropped, and anything not
// recognised becomes /other.
//
// Off unless VITE_PLAUSIBLE_DOMAIN is set, and always off with the dev sign-in.

type Plausible = ((event: string, options?: { u?: string }) => void) & { q?: unknown[] };

declare global {
  interface Window {
    plausible?: Plausible;
  }
}

const DOMAIN = (import.meta.env.VITE_PLAUSIBLE_DOMAIN as string | undefined)?.trim();
// The script variant that sends no page view of its own: we send each one, with a clean URL.
const SCRIPT = ((import.meta.env.VITE_PLAUSIBLE_SCRIPT as string | undefined)?.trim()
  || "https://plausible.io/js/script.manual.js");
const DEV = Boolean(import.meta.env.VITE_DEV_USER);

export const analyticsEnabled = Boolean(DOMAIN) && !DEV;

/** Workspace tabs that may appear in a page type. Kept equal to ClientWorkspace's tabs by a test. */
export const ANALYTICS_TABS = [
  "overview", "positions", "holdings", "compare", "stress",
  "profile", "assistant", "report", "review", "audit",
] as const;

/** The page type reported for an address inside the signed-in app. */
export function pageType(pathname: string, search = ""): string {
  if (pathname === "/" || pathname === "") return "/clients";
  if (/^\/clients\/[^/]+\/?$/.test(pathname)) {
    const tab = new URLSearchParams(search).get("tab") ?? "overview";
    return (ANALYTICS_TABS as readonly string[]).includes(tab)
      ? `/clients/:client/${tab}` : "/clients/:client/other";
  }
  return "/other";
}

let loaded = false;

function load() {
  if (loaded || !analyticsEnabled) return;
  loaded = true;
  // Queue calls made before the script arrives (Plausible's documented stub).
  window.plausible = window.plausible || Object.assign(
    (...args: unknown[]) => { (window.plausible!.q = window.plausible!.q || []).push(args); }, {});
  const s = document.createElement("script");
  s.defer = true;
  s.src = SCRIPT;
  s.dataset.domain = DOMAIN!;
  document.head.appendChild(s);
}

let last = "";

/** Report one page view of `type` (a value from pageType, or a fixed public page). `key`
 *  is the real address, used only here to skip repeats (React may run an effect twice);
 *  it is never sent. */
export function trackPageview(type: string, key: string = type) {
  if (!analyticsEnabled || key === last) return;
  last = key;
  load();
  window.plausible?.("pageview", { u: `${window.location.origin}${type}` });
}

/** For tests: forget the last page view. */
export function _resetAnalytics() {
  last = "";
}
