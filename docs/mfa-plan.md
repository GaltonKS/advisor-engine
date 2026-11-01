# Plan: multi-factor sign-in

Status: agreed, not built. The open questions are settled (see "Decisions" at the end).

## Why

Advisers' accounts open client holdings, risk profiles and audit trails. A password alone
is the weakest point in that chain: reused or phished passwords are the usual way such
accounts are taken over. Regulators expect firms handling client records to protect access
to them (the SEC's amended Regulation S-P, FINRA cybersecurity guidance), and firms that
evaluate GaltonKS will ask whether multi-factor sign-in is required. The migration plan
already lists MFA as "not yet" (`docs/migration-plan.md`).

## Where we start

- Sign-in is Supabase Auth, email and password, with "Confirm email" on
  (`web/src/lib/auth.tsx`, `web/src/pages/SignIn.tsx`).
- The API verifies the Supabase access token (`api/auth.py`, `verify_supabase_token`):
  signature, audience `authenticated`, issuer, expiry, and rejects anonymous sessions. It
  does not look at how strongly the user signed in.
- The API then runs every query as `authenticated` with claims it builds itself
  (`SupabaseSession` in `api/supabase_store.py`: `sub`, `email`, `role` only), and
  row-level security keys on `app.current_firm_id()`.
- Dev auth (`X-Dev-User`) is local only and refuses to start on Render.

## Recommendation in one paragraph

Use Supabase Auth's built-in MFA with **authenticator-app codes (TOTP)**, make it
**required for every adviser**, and enforce it on the **server**, not only in the
browser: the API refuses any token whose assurance level is not `aal2`, and the database
refuses it too. The web app adds two screens, "Set up your authenticator" and "Enter your
code". Recovery is handled by asking each adviser to enrol a second authenticator, plus an
audited operator reset. No new vendor, no SMS, no new secrets.

## How Supabase MFA works (the parts we rely on)

- A user enrols a **factor** (a TOTP secret shown as a QR code). Enrolment is only complete
  once they enter a valid code from it.
- Signing in with a password gives a session at **assurance level 1 (`aal1`)**. Answering a
  challenge for a verified factor upgrades the same session to **`aal2`**.
- The level is written into the access token as the `aal` claim, with the methods used in
  `amr` (for example password, then totp). Tokens refreshed from an `aal2` session stay
  `aal2`; a password reset or a new sign-in starts again at `aal1`.
- The JS client reports the current and next level ("you are aal1, you could reach aal2"),
  which is how the app decides which screen to show.
- Removing a verified factor requires an `aal2` session, so a stolen password alone cannot
  strip MFA from an account.
- TOTP is available on all Supabase plans at no extra cost. Phone (SMS) MFA costs per
  message and needs an SMS provider. Supabase also offers an "MFA verification attempt"
  hook on paid plans for custom lock-out rules.

## Choices

| Question | Recommendation | Why |
|---|---|---|
| Which second factor | TOTP authenticator apps (Google Authenticator, Microsoft Authenticator, 1Password, Authy) | Free, works offline, no phone number to store, resistant to SIM swap. |
| SMS codes | Not offered | Weaker (SIM swap, interception), costs per message, and adds a phone number to the personal data we hold and must cover in the Privacy Policy. |
| Passkeys / security keys | Later, as an extra factor type once Supabase's support is generally available | Strongest and phishing-resistant, but should not block the first release. |
| Required or optional | Required for every adviser, after a short enrolment grace period | Optional MFA is rarely turned on; firms will ask whether it is enforced. |
| Where it is enforced | API and database, with the browser only guiding the user | A browser check can be skipped by calling the API directly. |
| Pending sign-ups | Must enrol MFA before submitting their registration | Registration records CRD numbers and firm names; it should be protected too, and approved accounts then never exist without MFA. |
| Dev auth and tests | Unaffected; dev auth has no MFA | Dev auth is local only and already refused on Render. |

## Server side

### API (`api/auth.py`, `api/settings.py`)

1. **Read the assurance level.** `verify_supabase_token` already decodes the claims; it
   should also carry `aal` (and `amr`) on the `User` it returns.
2. **A setting to roll it out.** A new environment variable, for example
   `ADVISOR_REQUIRE_MFA`, with three values:
   - `off`: today's behaviour.
   - `enrol`: tokens at `aal1` are still accepted, but `/me` reports that MFA is not set up
     so the web app pushes the adviser to enrol. Used for the grace period.
   - `required`: every route except `/me` answers **403 with a distinct reason**
     ("Multi-factor sign-in required") for an `aal1` token. Default on Render once rolled
     out; `load_settings` should refuse `off` on Render after that, the same way it refuses
     dev auth there.
3. **Where the check sits.** Alongside the existing approval gate: `any_ctx` is the right
   level, so pending advisers are covered as well, with `/me` the one route that answers at
   `aal1` (the app needs it to know which screen to show). The 403 must be distinguishable
   from "awaiting approval" so the web app can route to the right screen.
4. **`/me` reports MFA state**: whether the session is `aal2`, and whether MFA is
   required. The browser can also learn this from Supabase directly, but having the API
   say it keeps one source of truth for what the server will accept.
5. **Forward the claim to the database.** `SupabaseSession` builds its own claims for row-
   level security; it should pass `aal` through with `sub`, `email` and `role`, so the
   database sees the same level the API verified.

### Database (`supabase/migrations/`)

A new migration (never edit an applied one) that adds a **restrictive** policy on every
firm-scoped table (firms, advisers, clients, portfolios, drafts, audit, questionnaire and
version tables): rows are visible or writable only when the token's `aal` claim is `aal2`.
Restrictive policies are combined with AND, so this sits on top of the firm policies
rather than replacing them. This keeps the rule true even if a future route or a direct
Supabase client call skips the API check. The `market` schema needs nothing: no adviser
can reach it.

Exceptions to think through in that migration: `app.ensure_adviser()` and
`app.submit_registration()` run before enrolment is guaranteed, so either they check the
level themselves (matching the API rule that registration needs `aal2`) or they are the
documented `aal1` exceptions. Recommendation: `ensure_adviser` stays usable at `aal1` (it
only creates or reads the caller's own pending row); `submit_registration` requires `aal2`.

The shim used in tests (`tests/supabase_shim.sql`) builds tokens from `request.jwt.claims`,
so the test fixtures will set `aal` for the approved dev users.

### Audit

Supabase keeps its own auth audit log (factor enrolled, challenge verified, factor
removed). In addition, record in our audit trail, per firm:

- `mfa_enrolled` / `mfa_factor_removed`, reported by the web app after Supabase confirms
  and checked by the API against the token's `amr` where possible;
- `mfa_reset_by_operator` for every operator reset (below), with who asked, who approved
  and how identity was checked.

Every audit record from the API can also carry the session's `aal`, so a compliance
reviewer can see that each action was taken from an MFA-verified session.

## Web app (`web/`)

The flow after a correct password:

1. **No verified factor yet** → "Set up your authenticator" screen: a QR code and the
   secret as text (for people who can't scan), a field for the first 6-digit code, and a
   friendly name for the device. On success, offer at once to add a **second
   authenticator** as the backup (strongly encouraged, see Recovery).
2. **Has a factor, session at `aal1`** → "Enter your code" screen: one 6-digit field,
   numeric keyboard on phones, autofill hint for one-time codes, a clear error on a wrong
   or expired code, and a "Lost your device?" link to the recovery instructions.
3. **Session at `aal2`** → the app as today.

Where it lives:
- `lib/auth.tsx` gains the assurance-level state (current and next level from Supabase),
  and enrol / challenge / verify / list-factors / unenrol actions. It keeps the rule that
  the browser holds only the publishable key.
- `main.tsx` routes on it before the pending-approval screen: password → MFA → approval
  → app. The API's 403 "Multi-factor sign-in required" also sends the user to the code
  screen, in case the session lost its level.
- A **Security** section in account settings: list enrolled authenticators with names and
  dates, add one, remove one (Supabase requires `aal2` to remove, and the last one cannot
  be removed while MFA is required).
- Copy: plain, short, and on brand; navy band header, gold primary button, as the sign-in
  page. The sign-in page's promise list can add "Two-step sign-in for every adviser."
- The desktop app (`desktop/`) loads the web app, so it gets the same screens with no
  separate work. The QR secret is shown in the page only and never stored.

## Recovery

Supabase has no built-in backup codes, so recovery is a process, not a feature:

1. **Prevent it:** at enrolment, ask for a second authenticator (a second phone, or a
   password manager with TOTP). Show a reminder in Security while only one is enrolled.
2. **Operator reset:** an adviser who has lost every factor contacts support. The operator
   verifies identity out of band (a call to the number on file with the firm, matching
   the CRD on FINRA BrokerCheck, and confirmation from the firm's owner for multi-adviser
   firms), then removes the adviser's factors with the Supabase admin API from an
   operator command next to the existing approval command (`api/admin.py`). The adviser
   then signs in with their password and enrols again. Every reset is audited.
3. Never reset from a request made inside the app or by email alone; that would turn
   recovery into the easiest way around MFA.

Optional later: our own single-use backup codes (stored hashed, shown once), if support
volume justifies it.

## Supabase project settings

- Auth → Multi-Factor: TOTP enabled; phone disabled.
- Keep "Confirm email" on (already relied on by the API).
- Consider shorter JWT expiry and session time-boxing (Auth → Sessions) so a stolen
  refresh token does not live long; and enable leaked-password protection.
- Rate limits on verification attempts are on by default; review them, and use the MFA
  verification hook (paid plans) if we want account lock-out after repeated failures.

## Rollout

1. Ship the web screens and Security settings with `ADVISOR_REQUIRE_MFA=off`; advisers
   can enrol voluntarily.
2. Switch to `enrol`: everyone signing in is prompted to set up MFA, and is told the date
   it becomes required, **one week** later. Watch how many advisers have enrolled (firm
   owners can see this for their own firm, below).
3. Switch to `required` and apply the database migration. Sessions already at `aal1` get
   the code screen on their next request; nobody is signed out mid-task without a path
   back in.
4. Update `CLAUDE.md` (API and Web conventions), `web/README.md`, `supabase/README.md`
   and the migration plan's "not yet" line. Check with whoever owns the Privacy Policy
   whether enrolment data (factor names and dates, no phone numbers) needs a mention; the
   policy text itself changes only from a new approved version.

## Tests to add (when it is built)

- API: tokens signed with the test HS256 secret at `aal1` and `aal2`; in `required` mode
  `aal1` gets the MFA 403 on every route except `/me`, and `aal2` behaves as today; in
  `enrol` and `off` modes `aal1` is accepted; the MFA 403 is distinct from the approval
  403; `load_settings` refuses `off` on Render once that rule is added.
- Database (`tests/test_supabase_store.py`, real Postgres): with claims at `aal1`, every
  firm-scoped table returns no rows and refuses writes even without a firm filter; at
  `aal2` the existing tests pass unchanged; `submit_registration` refuses `aal1`.
- Web: routing between password, enrol, code, pending and app states; the API's MFA 403
  sends the user to the code screen; removing the last factor is not offered while MFA is
  required.
- Dev auth tests stay as they are.

## Decisions

Settled after review of the first draft.

### 1. Grace period: one week

The `enrol` stage lasts **one week**, then `required` is switched on. Advisers are told the
date from their first sign-in in `enrol` mode, and the enrol screen can be postponed
("Remind me next time") until that date, not after. On the date, the screen can no longer
be skipped. The date is a deployment decision (when the operator changes the setting),
not something the app counts down itself.

### 2. MFA is required at every firm, and firms can see who has enrolled

- MFA is required for every adviser at every firm. There is no per-firm switch to turn it
  off.
- A firm's owner (and, if we add the role, its compliance officer) can see which of the
  firm's advisers have enrolled: name, email, whether MFA is set up, how many
  authenticators, and when the latest one was added. The view never shows secrets or QR
  codes, and it lists only the firm's own advisers (row-level security on
  `app.current_firm_id()`, as for every other table).
- Supabase keeps factors in its `auth` schema, which advisers cannot read. Enrolment status
  is therefore exposed through one narrow, security-definer function (or view) that
  returns only the fields above, for the caller's own firm, to an `aal2` session. The API
  serves it on a new route, and the web app shows it in the firm's settings, for example
  as a "Two-step sign-in" column next to each adviser.
- Advisers who haven't enrolled yet during the grace week are shown as "Not set up", so an
  owner can chase them before the deadline.
- Enrolment changes (`mfa_enrolled`, `mfa_factor_removed`, `mfa_reset_by_operator`) appear
  in the firm's audit trail, so the view can be checked against a history.

### 3. Re-enter a code for sensitive actions

Some actions need a **fresh code even inside an `aal2` session**:

- Deleting a client (`DELETE /clients/{id}`). This is the agreed first case.
- Proposed for the same treatment, to confirm when building: removing an authenticator,
  changing the account email or password, and exporting client data in bulk if that
  feature is added.

How it works:
- The access token's `amr` claim records each sign-in method with the time it was used.
  For a sensitive route, the API requires a TOTP entry no older than a short window (for
  example five minutes). Otherwise it answers **403 with its own reason** ("Enter your
  code to confirm this action"), distinct from the other 403s.
- The web app catches that reason, asks for a code in a small confirmation dialog
  ("Delete Jane Smith? Enter your authenticator code to confirm"), verifies it with
  Supabase (which issues a fresh token), and retries the same request once.
- The check is on the server, so calling the API directly cannot skip it. The time window
  lives in one constant in the API, with tests for fresh, stale and missing codes.
- The audit record for the action notes that a fresh code was used.

### 4. Passkeys later, as a second factor

Passkeys (and hardware security keys) are kept on the plan as an **additional factor
type**, next to authenticator codes, not a replacement. They are added once Supabase's
support is generally available and works across the browsers and the desktop app we
support. Until then, TOTP is the only factor. When they arrive, either factor satisfies
`aal2` and the fresh-code check for sensitive actions.
