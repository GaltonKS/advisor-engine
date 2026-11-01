-- Close the two ways round the API's fresh-code check (docs/mfa-plan.md, "Known gaps").
--
-- 1. Removing an authenticator. Supabase lets a browser holding an aal2 session call
--    `supabase.auth.mfa.unenroll` directly, with no fresh code and no audit row. Supabase
--    Auth deletes the row from auth.mfa_factors in both cases (the adviser's own call and
--    the API's admin call), so a trigger there refuses to delete a *verified* factor
--    unless the API has just issued a one-time permit for it, which it does only after
--    its own checks (fresh code, never the last factor while required). Permits live two
--    minutes and are used up by the delete. Unverified factors (abandoned set-ups, which
--    the browser tidies away) and deletes cascading from a user's deletion are allowed.
--    The operator's reset-mfa command issues permits the same way.
--
-- 2. Deleting a client. `authenticated` can UPDATE public.clients, so if the public
--    schema is exposed to Supabase's REST API a client could be soft-deleted there
--    without a fresh code. A trigger now requires one for any change to deleted_at /
--    deleted_by made as `authenticated` (or `anon`), whichever way the query arrives: the
--    API forwards the token's `amr` claim with the verified `aal`, so its own deletes are
--    checked here too. The rule is the API's (auth.User.mfa_fresh, FRESH_MFA_SECONDS):
--      off      -> no code needed
--      enrol    -> a code within 300 seconds, from sessions that have a second factor
--      required -> a code within 300 seconds
--    The mode is the database's own (app.security_settings), as for app.mfa_ok().
--
-- Errors use SQLSTATE PT403, which Supabase's REST layer answers as HTTP 403.

-- --- 1. authenticators ----------------------------------------------------------------

create table app.mfa_factor_removals (
    user_id        uuid not null,
    factor_id      text not null,
    allowed_until  timestamptz not null,
    primary key (user_id, factor_id)
);
alter table app.mfa_factor_removals enable row level security;
revoke all on app.mfa_factor_removals from public, anon, authenticated;

-- API (owner connection) only, after its own checks have passed.
create function app.allow_factor_removal(p_user uuid, p_factor text) returns void
language sql security definer set search_path = '' as $$
    delete from app.mfa_factor_removals where allowed_until < now();
    insert into app.mfa_factor_removals (user_id, factor_id, allowed_until)
        values (p_user, p_factor, now() + interval '2 minutes')
        on conflict (user_id, factor_id) do update set allowed_until = excluded.allowed_until;
$$;
revoke execute on function app.allow_factor_removal(uuid, text) from public, anon, authenticated;

-- Runs as the owner: Supabase Auth's own role has no rights on the app schema.
create function app.guard_mfa_factor_delete() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    -- Not yet a second factor, so removing it weakens nothing.
    if old.status::text <> 'verified' then
        return old;
    end if;
    -- Cascading from the user's own deletion (a foreign-key trigger is the caller), or
    -- part of a soft-deleted user's clean-up.
    if pg_trigger_depth() > 1
       or exists (select from auth.users u where u.id = old.user_id and u.deleted_at is not null) then
        return old;
    end if;
    delete from app.mfa_factor_removals
     where user_id = old.user_id and factor_id = old.id::text and allowed_until >= now();
    if not found then
        raise exception 'Remove authenticators from the Security page in GaltonKS, which asks for a fresh code.'
            using errcode = 'PT403';
    end if;
    return old;
end
$$;
revoke execute on function app.guard_mfa_factor_delete() from public, anon, authenticated;

create trigger guard_mfa_factor_delete before delete on auth.mfa_factors
    for each row execute function app.guard_mfa_factor_delete();

-- --- 2. client deletion ---------------------------------------------------------------

-- True when the caller's token shows a code fresh enough for a sensitive action.
create function app.mfa_fresh_ok() returns boolean
language sql stable security definer set search_path = '' as $$
    with s as (select coalesce((select mfa_mode from app.security_settings), 'off') as mode),
    fresh as (
        select exists (
            select from jsonb_array_elements(
                case when jsonb_typeof(auth.jwt() -> 'amr') = 'array'
                     then auth.jwt() -> 'amr' else '[]'::jsonb end) e
             where jsonb_typeof(e) = 'object'
               and e ->> 'method' in ('totp', 'mfa/totp', 'webauthn', 'mfa/webauthn')
               and (e ->> 'timestamp') ~ '^[0-9]{1,12}$'
               and (e ->> 'timestamp')::bigint >= extract(epoch from now()) - 300
        ) as ok
    )
    select case s.mode
               when 'off' then true
               when 'enrol' then coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' or fresh.ok
               else fresh.ok
           end
      from s, fresh
$$;
grant execute on function app.mfa_fresh_ok() to authenticated;
revoke execute on function app.mfa_fresh_ok() from public, anon;

-- Security invoker on purpose: current_user is then the caller's role, so the owner's
-- connection (operator commands, migrations) is not asked for a code.
create function app.guard_client_delete() returns trigger
language plpgsql set search_path = '' as $$
begin
    if (old.deleted_at is distinct from new.deleted_at
        or old.deleted_by is distinct from new.deleted_by)
       and current_user in ('authenticated', 'anon')
       and not app.mfa_fresh_ok() then
        raise exception 'Enter your authenticator code to confirm deleting this client.'
            using errcode = 'PT403';
    end if;
    return new;
end
$$;

create trigger guard_client_delete before update on public.clients
    for each row execute function app.guard_client_delete();
