-- Households and the three-part risk profile (willingness, ability, need).
--
-- - public.people: the one or two people in a client's household. `role` is `primary`
--   or `partner`, unique per client, so a household holds at most two people and at
--   most one of each; the database enforces it, not just the store.
-- - public.profile_inputs: the adviser's answers to the profile questions, one row per
--   answer. Answers are stored, not scores: the engine (advisor_engine.profile)
--   recomputes the profile on every read. The latest row per (client, person, question)
--   wins, ordered by recorded_at then id; a null `answer` retracts that question.
--   `person_id` is set for willingness (a per-person dimension) and null for ability
--   (per household) and need (per plan). `source` is only `advisor_entered` for now.
-- - public.profile_snapshots: a frozen engine result (`result`) with the method
--   version and a hash of the inputs it was computed from.
--
-- profile_inputs and profile_snapshots are append-only, like audit_events: advisers may
-- select and insert their firm's rows, nothing else, and triggers refuse UPDATE, DELETE
-- and TRUNCATE for everyone (the owner too, unless they drop the triggers). A correction
-- is a new row. people rows can't be deleted by advisers either (no DELETE grant).
--
-- A soft-deleted client hides its people, inputs and snapshots the way it hides its
-- portfolios: the API resolves the client first (own_client) and answers 404 once
-- deleted_at is set. Nothing is removed.
--
-- Every table is keyed on app.current_firm_id() and gets the restrictive mfa_required
-- policy (20261106000000_mfa.sql), like the other firm tables.

create table public.people (
    id          uuid primary key default gen_random_uuid(),
    firm_id     uuid not null references public.firms (id),
    client_id   uuid not null references public.clients (id),
    name        text not null check (length(name) between 1 and 200),
    role        text not null check (role in ('primary', 'partner')),
    created_by  uuid not null references public.advisers (id),
    created_at  timestamptz not null default now(),
    unique (client_id, role)
);
create index on public.people (firm_id);
create index on public.people (firm_id, client_id);

create table public.profile_inputs (
    id              bigint generated always as identity primary key,
    firm_id         uuid not null references public.firms (id),
    client_id       uuid not null references public.clients (id),
    person_id       uuid references public.people (id),
    dimension       text not null check (dimension in ('willingness', 'ability', 'need')),
    question_id     text not null check (length(question_id) between 1 and 100),
    answer          jsonb,                      -- null: the answer was retracted
    source          text not null default 'advisor_entered'
                        check (source in ('advisor_entered')),
    entered_by      uuid not null references public.advisers (id),
    method_version  text not null,
    -- clock_timestamp, not now(): several answers saved in one transaction still order.
    recorded_at     timestamptz not null default clock_timestamp(),
    -- Willingness is per person; ability (household) and need (plan) are not.
    check ((dimension = 'willingness') = (person_id is not null))
);
create index on public.profile_inputs (firm_id);
create index on public.profile_inputs (firm_id, client_id, recorded_at);

create table public.profile_snapshots (
    id              uuid primary key default gen_random_uuid(),
    firm_id         uuid not null references public.firms (id),
    client_id       uuid not null references public.clients (id),
    created_by      uuid not null references public.advisers (id),
    method_version  text not null,
    inputs_hash     text not null,
    result          jsonb not null,
    created_at      timestamptz not null default now()
);
create index on public.profile_snapshots (firm_id);
create index on public.profile_snapshots (firm_id, client_id, created_at);

-- --- append-only ----------------------------------------------------------------------

create function app.profile_append_only() returns trigger
language plpgsql as $$
begin
    raise exception '% is append-only: add a new row instead', tg_table_name;
end
$$;
create trigger profile_inputs_no_update before update or delete on public.profile_inputs
    for each row execute function app.profile_append_only();
create trigger profile_inputs_no_truncate before truncate on public.profile_inputs
    for each statement execute function app.profile_append_only();
create trigger profile_snapshots_no_update before update or delete on public.profile_snapshots
    for each row execute function app.profile_append_only();
create trigger profile_snapshots_no_truncate before truncate on public.profile_snapshots
    for each statement execute function app.profile_append_only();
revoke execute on function app.profile_append_only() from public, anon, authenticated;

-- --- row-level security ---------------------------------------------------------------

alter table public.people            enable row level security;
alter table public.profile_inputs    enable row level security;
alter table public.profile_snapshots enable row level security;

create policy firm_rows on public.people for all to authenticated
    using (firm_id = app.current_firm_id()) with check (firm_id = app.current_firm_id());

create policy firm_read on public.profile_inputs for select to authenticated
    using (firm_id = app.current_firm_id());
create policy firm_append on public.profile_inputs for insert to authenticated
    with check (firm_id = app.current_firm_id());

create policy firm_read on public.profile_snapshots for select to authenticated
    using (firm_id = app.current_firm_id());
create policy firm_append on public.profile_snapshots for insert to authenticated
    with check (firm_id = app.current_firm_id());

create policy mfa_required on public.people as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.profile_inputs as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.profile_snapshots as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));

-- --- grants ---------------------------------------------------------------------------

revoke all on public.people, public.profile_inputs, public.profile_snapshots
    from public, anon, authenticated;
grant select, insert, update on public.people to authenticated;
grant select, insert on public.profile_inputs, public.profile_snapshots to authenticated;
