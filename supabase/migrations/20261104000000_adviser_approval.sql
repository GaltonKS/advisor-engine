-- Adviser approval: new sign-ups wait as `pending` until the platform operator verifies
-- them (CRD number on FINRA BrokerCheck) and approves.
--
-- - app.ensure_adviser now creates `pending` advisers. Existing advisers keep their status.
--   A pending adviser resolves to no firm (app.current_firm_id() is null), so row-level
--   security already shows them nothing and refuses their writes.
-- - app.submit_registration lets a pending adviser record their name, CRD number and firm
--   name for review. It changes only their own row, and only while pending.
-- - app.approve_adviser activates an adviser, optionally moving them into an existing firm
--   as a member. It is for the operator only (SQL Editor or `python -m
--   advisor_engine.api.admin`): advisers cannot run it.

alter table public.advisers
    add column full_name text,
    add column registration_submitted_at timestamptz,
    add column approved_at timestamptz;

alter table public.firms
    add column requested_name text;  -- the firm name the adviser gave at registration

-- New sign-ups are pending. Same as before otherwise (idempotent, creates their own firm).
create or replace function app.ensure_adviser() returns public.advisers
language plpgsql security definer set search_path = '' as $$
declare
    uid   uuid := auth.uid();
    email text := coalesce(auth.jwt() ->> 'email', '');
    row   public.advisers;
    fid   uuid;
begin
    if uid is null then
        raise exception 'not signed in';
    end if;
    select * into row from public.advisers where id = uid;
    if found then
        return row;
    end if;
    insert into public.firms (name)
        values (coalesce(nullif(split_part(email, '@', 2), ''), 'My firm'))
        returning id into fid;
    insert into public.advisers (id, email, firm_id, status)
        values (uid, email, fid, 'pending')
        returning * into row;
    return row;
end
$$;

-- A pending adviser's registration details, for the operator to verify.
create function app.submit_registration(p_full_name text, p_crd text, p_firm_name text)
returns public.advisers
language plpgsql security definer set search_path = '' as $$
declare
    row public.advisers;
begin
    if auth.uid() is null then
        raise exception 'not signed in';
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

-- Operator only: activate an adviser, optionally as a member of an existing firm.
create function app.approve_adviser(p_email text, p_firm uuid default null)
returns public.advisers
language plpgsql security definer set search_path = '' as $$
declare
    row public.advisers;
begin
    update public.advisers
       set status = 'active', approved_at = now(),
           firm_id = coalesce(p_firm, firm_id),
           role = case when p_firm is null then role else 'member' end
     where lower(email) = lower(p_email) and status = 'pending'
     returning * into row;
    if not found then
        raise exception 'no pending adviser with email %', p_email;
    end if;
    if p_firm is null then
        -- Their own new firm: take the name they registered, if any.
        update public.firms set name = coalesce(requested_name, name) where id = row.firm_id;
    end if;
    return row;
end
$$;

grant execute on function app.submit_registration(text, text, text) to authenticated;
revoke execute on function app.submit_registration(text, text, text) from public, anon;
revoke execute on function app.approve_adviser(text, uuid) from public, anon, authenticated;
