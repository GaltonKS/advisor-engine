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

Then **Redirects/Rewrites**: add a rewrite from `/*` to `/index.html` so links like
`/clients/<id>` load the app. After the first deploy, add the site's address to the API's
`ALLOWED_ORIGINS` and to Supabase **Authentication → URL Configuration** (Site URL and
Redirect URLs).
