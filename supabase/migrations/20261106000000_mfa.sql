-- Multi-factor sign-in, enforced in the database as well as the API (docs/mfa-plan.md).
--
-- Supabase writes how strongly a user signed in into the token's `aal` claim: "aal1"
-- after a password alone, "aal2" once they have entered an authenticator code. The API
-- forwards the level it verified (SupabaseSession), and direct Supabase client calls
-- carry the real token, so the rule below holds whichever way a query arrives.
--
-- - app.security_settings holds the mode: off | enrol | required. It starts `off`, so
--   applying this migration changes nothing until the operator switches it on (after
--   the API's ADVISOR_REQUIRE_MFA, never before: see `python -m advisor_engine.api.admin
--   mfa-mode`). Advisers cannot read or change it.
-- - In `required` mode, a restrictive policy on every firm-scoped table hides all rows
--   and refuses all writes to a session below aal2. Restrictive policies are ANDed with
--   the firm policies, so this adds to them and never widens them.
-- - SECURITY DEFINER functions bypass those policies, so the ones an adviser can call
--   check the level themselves: app.submit_registration and app.bump_turns refuse below
--   aal2 in `required` mode. app.ensure_adviser still answers at aal1: it only creates or
--   reads the caller's own row, and the app needs it to know where to send them.

create table app.security_settings (
    id          boolean primary key default true check (id),  -- a single row
    mfa_mode    text not null default 'off' check (mfa_mode in ('off', 'enrol', 'required')),
    updated_at  timestamptz not null default now()
);
insert into app.security_settings default values;
revoke all on app.security_settings from public, anon, authenticated;

-- True when the caller's session meets the multi-factor rule in force.
create function app.mfa_ok() returns boolean
language sql stable security definer set search_path = '' as $$
    select coalesce((select mfa_mode from app.security_settings) <> 'required', true)
        or coalesce(auth.jwt() ->> 'aal', '') = 'aal2'
$$;

-- Operator only: change the mode.
create function app.set_mfa_mode(p_mode text) returns text
language sql security definer set search_path = '' as $$
    update app.security_settings set mfa_mode = p_mode, updated_at = now()
     returning mfa_mode
$$;

grant execute on function app.mfa_ok() to authenticated;
revoke execute on function app.mfa_ok() from public, anon;
revoke execute on function app.set_mfa_mode(text) from public, anon, authenticated;

-- --- restrictive policies -------------------------------------------------------------
-- `(select app.mfa_ok())` is evaluated once per query, not once per row.

create policy mfa_required on public.firms as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.advisers as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.clients as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.portfolios as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.conversations as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.drafts as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.audit_events as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.usage as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));

-- --- functions that bypass RLS --------------------------------------------------------

-- As before (20261104000000_adviser_approval.sql), plus the multi-factor check:
-- registration records a CRD number and firm name, so it needs a verified session.
create or replace function app.submit_registration(p_full_name text, p_crd text, p_firm_name text)
returns public.advisers
language plpgsql security definer set search_path = '' as $$
declare
    row public.advisers;
begin
    if auth.uid() is null then
        raise exception 'not signed in';
    end if;
    if not app.mfa_ok() then
        raise exception 'multi-factor sign-in required';
    end if;
    if coalesce(length(trim(p_full_name)), 0) not between 1 and 200
       or p_crd !~ '^[0-9]{1,8}$'
       or coalesce(length(trim(p_firm_name)), 0) not between 1 and 200 then
        raise exception 'invalid registration details';
    end if;
    update public.advisers
       set full_name = trim(p_full_name), crd = p_crd, registration_submitted_at = now()
     where id = auth.uid() and status = 'pending'
     returning * into row;
    if not found then
        raise exception 'registration is only for advisers awaiting approval';
    end if;
    update public.firms set requested_name = trim(p_firm_name) where id = row.firm_id;
    return row;
end
$$;

-- As before (20261031000000_firm_scoped_schema.sql), plus the multi-factor check.
create or replace function app.bump_turns(max_turns integer) returns boolean
language plpgsql security definer set search_path = '' as $$
declare
    n integer;
begin
    if app.current_firm_id() is null or not app.mfa_ok() then
        return false;
    end if;
    insert into public.usage (adviser_id, day, turns)
        values (auth.uid(), (now() at time zone 'utc')::date, 1)
        on conflict (adviser_id, day) do update set turns = public.usage.turns + 1
            where public.usage.turns < max_turns
        returning turns into n;
    return n is not null;
end
$$;
