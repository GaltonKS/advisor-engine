import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import manifestText from "../../public/manifest.webmanifest?raw";
import { useInstallPrompt } from "./install";

function InstallButton() {
  const install = useInstallPrompt();
  return install ? <button onClick={() => install()}>Install app</button> : null;
}

function offerInstall() {
  const prompt = vi.fn(() => Promise.resolve());
  const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
    prompt,
    userChoice: Promise.resolve({ outcome: "accepted" as const }),
  });
  act(() => {
    window.dispatchEvent(event);
  });
  return { event, prompt };
}

afterEach(() => {
  cleanup();
  delete (window as Window & { advisorDesktop?: unknown }).advisorDesktop;
  act(() => {
    window.dispatchEvent(new Event("appinstalled")); // clear any offer left by a test
  });
});

describe("Install app button", () => {
  it("is hidden until the browser offers install", () => {
    render(<InstallButton />);
    expect(screen.queryByText("Install app")).toBeNull();
  });

  it("shows the browser's prompt once, then hides", () => {
    render(<InstallButton />);
    const { event, prompt } = offerInstall();
    expect(event.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByText("Install app"));
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Install app")).toBeNull();
  });

  it("stays hidden inside the desktop shell", () => {
    (window as Window & { advisorDesktop?: unknown }).advisorDesktop = { isDesktop: true };
    render(<InstallButton />);
    offerInstall();
    expect(screen.queryByText("Install app")).toBeNull();
  });
});

describe("web app manifest", () => {
  const manifest = JSON.parse(manifestText);
  const publicFiles = Object.keys(import.meta.glob("../../public/*.png")).map((p) => p.replace("../../public", ""));

  it("meets the browsers' install requirements, in the brand navy", () => {
    expect(manifest.name).toBeTruthy();
    expect(manifest.start_url).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.theme_color).toBe("#000815");
    const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
  });

  it("points at icon files that exist", () => {
    for (const icon of manifest.icons) expect(publicFiles).toContain(icon.src);
  });
});
