// "Install app" support for the installable web app (manifest.webmanifest).
//
// Chrome and Edge fire `beforeinstallprompt` once the page qualifies for install,
// often before React has mounted, so the listener is attached when this module is
// imported (main.tsx imports it first) and the event is kept until a button uses it.
// Safari and Firefox never fire it; there the button simply doesn't appear and users
// install from the browser's own menu (Safari: File → Add to Dock).
import { useSyncExternalStore } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); // keep the browser's own mini-infobar from appearing; we offer a button
    deferred = e as BeforeInstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    notify();
  });
}

/** True when already running as the installed app or inside the desktop shell. */
export function runningAsApp(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as Window & { advisorDesktop?: { isDesktop?: boolean } };
  return Boolean(w.advisorDesktop?.isDesktop) || window.matchMedia?.("(display-mode: standalone)").matches === true;
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Returns an install function while the browser offers install, else null. */
export function useInstallPrompt(): (() => Promise<void>) | null {
  const available = useSyncExternalStore(subscribe, () => deferred !== null, () => false);
  if (!available || runningAsApp()) return null;
  return async () => {
    const event = deferred;
    if (!event) return;
    deferred = null; // a prompt can be shown only once
    notify();
    await event.prompt();
  };
}
