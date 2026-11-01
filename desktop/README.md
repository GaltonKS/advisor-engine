# GaltonKS desktop shell

A thin Electron window around the web app. It loads the site rather than bundling it,
so every web deploy reaches desktop users immediately; the shell only changes for
native features. It computes nothing and adds no API of its own: the page gets a
read-only `window.advisorDesktop` (`isDesktop`, `platform`, `shellVersion`) and no
Node access (context isolation, sandbox, no node integration).

## Try it locally

Three terminals, from the repo root. Requires Python >= 3.10 and Node 22.

1. API with dev sign-in (mock Galton AI, no keys needed):

```bash
pip install -e ".[api]"
```

```bash
uvicorn advisor_engine.api.main:app --reload
```

2. Web app:

```bash
cd web && npm install && VITE_DEV_USER=alice npm run dev
```

3. Desktop shell:

```bash
cd desktop && npm install && npm start
```

Run from source, the shell opens `http://localhost:5173`. If nothing answers there it
shows an offline page with a **Try again** button. To point it somewhere else (a
Render preview, say):

```bash
ADVISOR_APP_URL=https://your-app.onrender.com npm start
```

On Linux as root (containers), Chromium needs `npm start -- --no-sandbox`.

## What it lets into the window

Only the app's own origin and the Supabase project (`ADVISOR_SUPABASE_URL` overrides
it) load inside the window. Any other link opens in the default browser, and only
`http:`, `https:` and `mailto:` links are handed to the OS.

## Packaging

1. Set `PRODUCTION_URL` in `main.js` to the Render static site.
2. Add that site's origin to the API's `ALLOWED_ORIGINS` (it already is if the
   browser version works) and to Supabase redirect URLs.
3. `npm run dist` builds a DMG on macOS or an NSIS installer on Windows into `dist/`.
   Unsigned builds trigger Gatekeeper / SmartScreen warnings; signing and
   notarisation are not set up yet.

Packaged builds check GitHub Releases (`GaltonKS/advisor-engine`) for shell updates via
`electron-updater`; web changes need no release.
