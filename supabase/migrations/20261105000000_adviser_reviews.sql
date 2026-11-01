-- Approval audit trail: every decision on a new sign-up, kept for good.
--
-- - public.adviser_reviews records who reviewed whom, when, the decision, which
--   BrokerCheck checks were confirmed, the rejection reason and a note. It is append-only
--   for everyone: no rights for anon/authenticated, RLS on with no policies, and triggers
--   that refuse UPDATE, DELETE and TRUNCATE (even for the owner, unless they drop them).
-- - app.review_adviser approves or rejects a pending adviser and writes the record in one
--   transaction. Operator-only, called by the API's operator route with the operator's
--   signed-in email as reviewed_by.
-- - app.approve_adviser (SQL Editor / admin command) now goes through it too, recorded as
--   reviewed by 'sql:<database user>' with no checks ticked.
-- - Advisers can now be 'rejected': like pending, they resolve to no firm and see nothing.

alter table public.advisers drop constraint advisers_status_check;
alter table public.advisers add constraint advisers_status_check
    check (status in ('active', 'pending', 'suspended', 'rejected'));

create table public.adviser_reviews (
    id             bigint generated always as identity primary key,
    adviser_id     uuid not null references public.advisers (id),
    adviser_email  text not null,
    adviser_name   text,
    crd            text,
    decision       text not null check (decision in ('approved', 'rejected')),
    reviewed_by    text not null,            -- operator email, or 'sql:<db user>'
    reviewed_at    timestamptz not null default now(),
    checks         text[] not null default '{}',  -- BrokerCheck checks confirmed
    reason         text,                     -- rejection reason (preset key)
    summary        text not null,            -- one line, built from the checks or reason
    note           text,
    firm_id        uuid references public.firms (id)
);
create index on public.adviser_reviews (reviewed_at desc);

alter table public.adviser_reviews enable row level security;
revoke all on public.adviser_reviews from public, anon, authenticated;

create function app.adviser_reviews_append_only() returns trigger
language plpgsql as $$
begin
    raise exception 'adviser reviews are append-only';
end
$$;
create trigger adviser_reviews_no_update before update or delete on public.adviser_reviews
    for each row execute function app.adviser_reviews_append_only();
create trigger adviser_reviews_no_truncate before truncate on public.adviser_reviews
    for each statement execute function app.adviser_reviews_append_only();

create function app.review_adviser(
    p_email text, p_decision text, p_reviewed_by text, p_checks text[], p_reason text,
    p_summary text, p_note text default null, p_firm uuid default null)
returns public.adviser_reviews
language plpgsql security definer set search_path = '' as $$
declare
    adv public.advisers;
    rev public.adviser_reviews;
begin
    if p_decision not in ('approved', 'rejected') then
        raise exception 'decision must be approved or rejected';
    end if;
    if coalesce(trim(p_reviewed_by), '') = '' then
        raise exception 'reviewed_by is required';
    end if;
    select * into adv from public.advisers
     where lower(email) = lower(p_email) and status = 'pending' for update;
    if not found then
        raise exception 'no pending adviser with email %', p_email;
    end if;
    if p_decision = 'approved' then
        update public.advisers
           set status = 'active', approved_at = now(),
               firm_id = coalesce(p_firm, firm_id),
               role = case when p_firm is null then role else 'member' end
         where id = adv.id returning * into adv;
        if p_firm is null then
            update public.firms set name = coalesce(requested_name, name) where id = adv.firm_id;
        end if;
    else
        update public.advisers set status = 'rejected' where id = adv.id returning * into adv;
    end if;
    insert into public.adviser_reviews (adviser_id, adviser_email, adviser_name, crd, decision,
                                        reviewed_by, checks, reason, summary, note, firm_id)
        values (adv.id, adv.email, adv.full_name, adv.crd, p_decision, trim(p_reviewed_by),
                coalesce(p_checks, '{}'), p_reason, p_summary, nullif(trim(p_note), ''),
                adv.firm_id)
        returning * into rev;
    return rev;
end
$$;

-- The SQL Editor / admin command path, now recorded too.
create or replace function app.approve_adviser(p_email text, p_firm uuid default null)
returns public.advisers
language plpgsql security definer set search_path = '' as $$
declare
    row public.advisers;
begin
    perform app.review_adviser(p_email, 'approved', 'sql:' || session_user, '{}', null,
                               'Approved in the SQL editor (no checks recorded)', null, p_firm);
    select * into row from public.advisers where lower(email) = lower(p_email);
    return row;
end
$$;

revoke execute on function app.review_adviser(text, text, text, text[], text, text, text, uuid)
    from public, anon, authenticated;
revoke execute on function app.approve_adviser(text, uuid) from public, anon, authenticated;
