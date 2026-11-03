-- Households and the three-part risk profile (willingness, ability, need).
--
-- A client is a household of one or two people. The adviser records answers; the engine
-- (advisor_engine.profile) scores them on every read, so only answers are stored, never
-- scores. A snapshot freezes one engine result for the record.
--
-- - public.people: at most two per client, enforced by the role check plus
--   unique (client_id, role): one 'primary' and one 'partner'. A soft-deleted client
--   hides its people the same way it hides its portfolios: the API resolves the client
--   first (own_client) and answers 404 once `deleted_at` is set. Nothing is removed.
-- - public.profile_inputs: one row per answer given. Append-only: corrections and
--   retractions (answer = null) are new rows, and the latest row per
--   (client_id, person_id, question_id) wins, ordered by recorded_at then id. Resolving
--   that is the engine's job, not SQL's.
-- - public.profile_snapshots: frozen engine output with the method version and a hash of
--   the inputs it used. Append-only.
--
-- The two append-only tables get SELECT and INSERT only (like audit_events) and triggers
-- that refuse UPDATE, DELETE and TRUNCATE even for the owner (like adviser_reviews).
-- Advisers may insert only the columns they supply: ids and timestamps come from the
-- database, and entered_by / created_by must be the caller.
--
-- Rows point at their client through (client_id, firm_id), so a row can never attach to
-- another firm's client even though foreign-key checks bypass row-level security.

alter table public.clients add constraint clients_id_firm_key unique (id, firm_id);

-- --- tables ---------------------------------------------------------------------------

create table public.people (
    id          uuid primary key default gen_random_uuid(),
    firm_id     uuid not null references public.firms (id),
    client_id   uuid not null,
    name        text not null check (length(name) between 1 and 200),
    role        text not null check (role in ('primary', 'partner')),
    created_at  timestamptz not null default now(),
    foreign key (client_id, firm_id) references public.clients (id, firm_id),
    unique (client_id, role),
    unique (id, client_id, firm_id)
);
create index on public.people (firm_id, client_id);

create table public.profile_inputs (
    id              bigint generated always as identity primary key,
    firm_id         uuid not null references public.firms (id),
    client_id       uuid not null,
    person_id       uuid,
    dimension       text not null check (dimension in ('willingness', 'ability', 'need')),
    question_id     text not null check (length(question_id) between 1 and 100),
    answer          jsonb,                    -- null: the answer was retracted
    source          text not null default 'advisor_entered'
                        check (source in ('advisor_entered')),
    entered_by      uuid not null references public.advisers (id),
    method_version  text not null,
    recorded_at     timestamptz not null default now(),
    foreign key (client_id, firm_id) references public.clients (id, firm_id),
    foreign key (person_id, client_id, firm_id) references public.people (id, client_id, firm_id),
    -- Willingness is per person; ability and need are per household / plan.
    check ((dimension = 'willingness') = (person_id is not null))
);
create index on public.profile_inputs (firm_id, client_id, recorded_at);

create table public.profile_snapshots (
    id              uuid primary key default gen_random_uuid(),
    firm_id         uuid not null references public.firms (id),
    client_id       uuid not null,
    created_by      uuid not null references public.advisers (id),
    method_version  text not null,
    inputs_hash     text not null,
    result          jsonb not null,
    created_at      timestamptz not null default now(),
    foreign key (client_id, firm_id) references public.clients (id, firm_id)
);
create index on public.profile_snapshots (firm_id, client_id, created_at);

-- --- append-only ----------------------------------------------------------------------

create function app.profile_append_only() returns trigger
language plpgsql as $$
begin
    raise exception '% is append-only', tg_table_name;
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

-- --- row-level security ---------------------------------------------------------------

alter table public.people            enable row level security;
alter table public.profile_inputs    enable row level security;
alter table public.profile_snapshots enable row level security;

create policy firm_rows on public.people for all to authenticated
    using (firm_id = app.current_firm_id()) with check (firm_id = app.current_firm_id());

create policy firm_read on public.profile_inputs for select to authenticated
    using (firm_id = app.current_firm_id());
create policy firm_append on public.profile_inputs for insert to authenticated
    with check (firm_id = app.current_firm_id() and entered_by = auth.uid());

create policy firm_read on public.profile_snapshots for select to authenticated
    using (firm_id = app.current_firm_id());
create policy firm_append on public.profile_snapshots for insert to authenticated
    with check (firm_id = app.current_firm_id() and created_by = auth.uid());

-- Two-step sign-in, as on every other table (20261106000000_mfa.sql).
create policy mfa_required on public.people as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.profile_inputs as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));
create policy mfa_required on public.profile_snapshots as restrictive for all to authenticated
    using ((select app.mfa_ok())) with check ((select app.mfa_ok()));

-- --- grants ---------------------------------------------------------------------------

revoke all on public.people, public.profile_inputs, public.profile_snapshots
    from public, anon, authenticated;

-- People can be renamed later (UPDATE of name only); never deleted by advisers.
grant select on public.people to authenticated;
grant insert (firm_id, client_id, name, role) on public.people to authenticated;
grant update (name) on public.people to authenticated;

grant select on public.profile_inputs, public.profile_snapshots to authenticated;
grant insert (firm_id, client_id, person_id, dimension, question_id, answer, source,
              entered_by, method_version)
    on public.profile_inputs to authenticated;
grant insert (firm_id, client_id, created_by, method_version, inputs_hash, result)
    on public.profile_snapshots to authenticated;
