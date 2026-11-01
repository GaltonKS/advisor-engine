import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApi } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useInstallPrompt } from "../lib/install";
import type { Me } from "../lib/types";
import { pageType, trackPageview } from "../lib/analytics";
import { Logo } from "./Logo";
import PendingApproval from "../pages/PendingApproval";

export function Brand({ onDark = true }: { onDark?: boolean }) {
  return (
    <Link to="/" aria-label="GaltonKS Knowledge System, home">
      <Logo onDark={onDark} />
    </Link>
  );
}

export function useMe() {
  const api = useApi();
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
}

export default function Layout() {
  const { email, signOut, dev } = useAuth();
  const me = useMe();
  const location = useLocation();
  const pending = me.data?.adviser.status === "pending" || me.data?.adviser.status === "rejected";
  // A page type only (see lib/analytics.ts): never the real address, ids or query text.
  useEffect(() => {
    if (!me.data) return;
    trackPageview(pending ? "/awaiting-approval" : pageType(location.pathname, location.search),
                  pending ? "/awaiting-approval" : location.pathname + location.search);
  }, [me.data, pending, location.pathname, location.search]);
  const install = useInstallPrompt();
  const initials = (email ?? "?").slice(0, 2).toUpperCase();
  return (
    <div className="min-h-screen">
      <header className="no-print navy-band">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-6 px-4 py-3">
          <Brand />
          <nav className="flex gap-5 text-sm">
            <NavLink to="/" end className={({ isActive }) => (isActive ? "border-b-2 border-gold-bright pb-0.5 font-semibold text-card" : "text-on-navy-muted hover:text-card")}>
              Clients
            </NavLink>
            {me.data?.operator && (
              <NavLink to="/operator" className={({ isActive }) => (isActive ? "border-b-2 border-gold-bright pb-0.5 font-semibold text-card" : "text-on-navy-muted hover:text-card")}>
                Pilot metrics
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs">
            {install && (
              <button className="border border-gold-bright px-2 py-1 text-gold-bright hover:bg-gold-bright hover:text-navy" onClick={() => install()}
                      title="Install GaltonKS as an app with its own window">
                Install app
              </button>
            )}
            {me.data && (
              <span className={`border px-2 py-1 ${me.data.llm === "mock" ? "border-on-navy-muted/40 text-on-navy-muted" : "border-gold-bright text-gold-bright"}`}>
                Galton AI: {me.data.llm === "mock" ? "mock (scripted)" : "live"}
              </span>
            )}
            {dev && <span className="border border-[#e8a598]/60 px-2 py-1 text-[#f0b9ae]">Dev sign-in</span>}
            <span className="hidden border border-on-navy-muted/40 px-2 py-1 text-card sm:inline">{email}</span>
            <button className="flex h-8 w-8 items-center justify-center rounded-full bg-card text-[11px] font-semibold text-navy ring-1 ring-gold-bright ring-offset-2 ring-offset-navy" title="Sign out" onClick={() => signOut()}>
              {initials}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1240px] px-4 py-6">
        {pending && me.data ? <PendingApproval me={me.data} /> : me.data || me.error ? <Outlet /> : null}
      </main>
      <footer className="no-print mx-auto max-w-[1240px] border-t border-rule px-4 py-4 text-xs text-muted">
        Decision support for registered advisers. Not investment advice. Every figure is computed by the
        engine{me.data ? ` (v${me.data.engine_version})` : ""};{" "}
        {!me.data?.returns_source || me.data.returns_source === "synthetic"
          ? "returns are synthetic and illustrative until licensed market data is connected."
          : `returns are monthly, from ${me.data.returns_source} price history.`}{" "}
        <Link to="/privacy" className="underline hover:text-navy">Privacy Policy</Link>
      </footer>
    </div>
  );
}
