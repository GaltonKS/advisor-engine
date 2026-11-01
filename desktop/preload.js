// Runs before the page loads. Exposes a tiny, read-only flag so the React app can tell
// it's inside the desktop shell (e.g. to send the shell version for a minimum-version check).
const { contextBridge } = require("electron");

const versionArg = process.argv.find((a) => a.startsWith("--galtonks-shell-version="));

contextBridge.exposeInMainWorld(
  "advisorDesktop",
  Object.freeze({
    isDesktop: true,
    platform: process.platform,
    shellVersion: versionArg ? versionArg.split("=")[1] : "unknown",
  }),
);
