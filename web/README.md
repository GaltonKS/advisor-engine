# GaltonKS web app

React front end for the advisor engine API (Vite, React, TypeScript, TanStack Query,
Tailwind, Supabase sign-in). It lays out what the API returns and computes no figures:
`src/lib/format.ts` only formats engine numbers, and every before/after "change" is the
engine's own value (see `src/components/ui.test.tsx`).

## Screens

Sign in / request access → Clients → client workspace, with tabs:

| Tab | What it does | API |
|---|---|---|
| Overview | Headline cards, where the risk comes from, sectors, downside, fit with the client's risk level | `GET /portfolios/{id}/profile`, `/suitability` |
| Positions | Sortable table; click a row for detail, ask the assistant about it | profile |
| Holdings & what-if | Edit positions, rerun the engine on the edits, save as a new version | `/holdings`, `/what-if`, `POST /clients/{id}/portfolios/holdings` |
| Compare | Any two versions side by side | `/compare/{other}` |
| Stress test | Preset or custom sector shocks | `/stress/scenarios`, `/stress` |
| Risk profile | Six-question questionnaire, scored by the engine (draft method) | `/questionnaire`, `PUT /clients/{id}/questionnaire` |
| Assistant | Chat with tool chips and sources; draft, edit and approve the client explanation | `/messages`, `/turns`, `/drafts/{id}/approve` |
| Client report | The approved explanation and key figures, printable to PDF | `/drafts` |
| Review prep | What changed since the previous version, fit, recent activity | compare, suitability, `/audit` |
| Audit trail | Every engine run and reply for this version | `/audit` |

## Run locally

Start the API with dev sign-in from the repo root (`uvicorn advisor_engine.api.main:app`,
no `SUPABASE_URL` set), then:

```bash
cd web && npm install
```

```bash
VITE_DEV_USER=alice npm run dev
```

Open http://localhost:5173. `VITE_DEV_USER` skips Supabase and sends `X-Dev-User`; it
only works against a dev-auth API, which always uses the mock assistant. To sign in for
real, copy `.env.example` to `.env.local` and set the Supabase URL and publishable key
instead.

```bash
npm test        # unit tests (vitest)
npm run build   # typecheck + production bundle in dist/
```

## Install as an app

The site is an installable web app (`public/manifest.webmanifest`): installed, it opens
in its own window with a GaltonKS icon in the Dock or taskbar, and it updates with
every deploy, since it is the same site.

- **Chrome / Edge:** the header shows **Install app** (from `src/lib/install.ts`),
  or use the install icon in the address bar.
- **Safari (macOS):** File → Add to Dock. **Firefox** doesn't install web apps.

The button hides when already installed or inside the desktop shell (`desktop/`).
There is deliberately no service worker: nothing is cached for offline use, so client
data never sits in a browser cache and nobody sees a stale version. Icons are the dark
app icon (`brand/galtonks-icon-dark.webp`) at 192 and 512px, plus a maskable 512px copy
with the icon inside the centre 80% safe zone.

## Deploy on Render (static site)

**New → Static Site**, repo `GaltonKS/advisor-engine`, branch `main`:

| Field | Value |
|---|---|
| Root Directory | `web` |
| Build Command | `npm ci && npm run build` |
| Publish Directory | `dist` |

Environment (none of these are secret; they ship to the browser):

| Key | Value |
|---|---|
| `NODE_VERSION` | `22` |
| `VITE_API_URL` | your API service, e.g. `https://advisor-engine-api.onrender.com` |
| `VITE_SUPABASE_URL` | `https://znkwkvcnxtahwinrxbvn.supabase.co` |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_…` |
| `VITE_PLAUSIBLE_DOMAIN` | optional: the site's domain as registered in Plausible (e.g. `app.galtonks.com`); turns on page-view analytics |
| `VITE_PLAUSIBLE_SCRIPT` | optional: Plausible script URL (default `https://plausible.io/js/script.manual.js`) |

Then **Redirects/Rewrites**: add a rewrite from `/*` to `/index.html` so links like
`/clients/<id>` load the app. After the first deploy, add the site's address to the API's
`ALLOWED_ORIGINS` and to Supabase **Authentication → URL Configuration** (Site URL and
Redirect URLs).
