-- The parts of a Supabase database the migrations rely on, for tests on plain Postgres.
-- auth.uid() / auth.jwt() read request.jwt.claims exactly as Supabase's do.

do $$
begin
    if not exists (select from pg_roles where rolname = 'anon') then
        create role anon nologin noinherit;
    end if;
    if not exists (select from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin noinherit;
    end if;
    if not exists (select from pg_roles where rolname = 'service_role') then
        create role service_role nologin noinherit bypassrls;
    end if;
end
$$;

create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;

create table if not exists auth.users (
    id     uuid primary key,
    email  text
);
alter table auth.users add column if not exists deleted_at timestamptz;

-- Supabase Auth's authenticators (only the columns the migrations use). Its `status` is an
-- enum there; the migrations compare it as text, so either works.
create table if not exists auth.mfa_factors (
    id             uuid primary key,
    user_id        uuid not null references auth.users (id) on delete cascade,
    friendly_name  text,
    status         text not null default 'unverified'
);

create or replace function auth.jwt() returns jsonb language sql stable as $$
    select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create or replace function auth.uid() returns uuid language sql stable as $$
    select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;

grant usage on schema public to anon, authenticated, service_role;
