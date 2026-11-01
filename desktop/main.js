// Thin desktop shell for GaltonKS.
// The window loads the hosted React app, so every website deploy updates the app
// immediately. The shell itself only changes for native features (rare).
//
// Which site it loads:
//   ADVISOR_APP_URL, if set
//   otherwise http://localhost:5173 when run from source (`npm start`), so it shows the
//     local dev stack (API with dev sign-in + `npm run dev` in web/)
//   otherwise PRODUCTION_URL in a packaged build

const { app, BrowserWindow, shell } = require("electron");
const path = require("path");
const { autoUpdater } = require("electron-updater");

// Your Render static site. Replace with the production URL before packaging.
const PRODUCTION_URL = "https://your-app.onrender.com";
const LOCAL_URL = "http://localhost:5173";

const APP_URL = process.env.ADVISOR_APP_URL || (app.isPackaged ? PRODUCTION_URL : LOCAL_URL);
const SUPABASE_URL = process.env.ADVISOR_SUPABASE_URL || "https://znkwkvcnxtahwinrxbvn.supabase.co";

// Pages allowed inside the window. Everything else opens in the default browser.
const ALLOWED_ORIGINS = [new URL(APP_URL).origin, new URL(SUPABASE_URL).origin];

const isAllowed = (url) => {
  try {
    return ALLOWED_ORIGINS.includes(new URL(url).origin);
  } catch {
    return false;
  }
};

// Only http(s) links are handed to the OS; never file:, javascript: or custom schemes.
const openExternal = (url) => {
  try {
    if (["http:", "https:", "mailto:"].includes(new URL(url).protocol)) shell.openExternal(url);
  } catch {
    // not a URL: ignore
  }
};

const offlinePage = path.join(__dirname, "offline.html");

function showOffline(win) {
  win.loadFile(offlinePage, {
    query: { retry: APP_URL, local: app.isPackaged ? "0" : "1" },
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: "GaltonKS",
    backgroundColor: "#000815",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, // page can't touch Node or Electron internals
      nodeIntegration: false,
      sandbox: true,
      // The sandboxed preload can't read package.json; hand it the version this way.
      additionalArguments: [`--galtonks-shell-version=${app.getVersion()}`],
    },
  });

  win.once("ready-to-show", () => win.show());
  win.loadURL(APP_URL);

  // Links to other sites (docs, email confirmations) open in the real browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowed(url)) return { action: "allow" };
    openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!isAllowed(url)) {
      event.preventDefault();
      openExternal(url);
    }
  });

  // No connection or the site is down: show a local page with a retry button.
  // -3 is ERR_ABORTED (a navigation replaced by another), not a failure.
  win.webContents.on("did-fail-load", (_e, code, _desc, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) showOffline(win);
  });
}

app.whenReady().then(() => {
  createWindow();
  // Updates the shell itself (rare) from GitHub Releases. UI updates need none of this.
  if (app.isPackaged) autoUpdater.checkForUpdatesAndNotify();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
