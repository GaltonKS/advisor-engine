-- Shared cache of month-end prices from the market data vendor (data/supabase_cache.py).
--
-- Market prices are not client data, so this is shared by every firm rather than keyed
-- on app.current_firm_id(). It is also licensed vendor data, which must not be readable
-- from a browser: it lives in its own schema, which Supabase's REST API does not expose,
-- with row-level security on and no policies, and no rights for anon or authenticated.
-- Only the API's own database connection (the schema owner) reads and writes it.

create schema if not exists market;

create table market.prices (
    source     text not null,             -- vendor label, e.g. 'FMP'
    symbol     text not null,
    month_end  date not null,
    close      double precision not null, -- dividend-adjusted close on the last trading day
    primary key (source, symbol, month_end)
);

-- When each symbol was last fetched: a symbol is fresh for a day (MAX_AGE_SECONDS).
create table market.fetches (
    source      text not null,
    symbol      text not null,
    fetched_at  timestamptz not null default now(),
    primary key (source, symbol)
);

alter table market.prices  enable row level security;
alter table market.fetches enable row level security;

revoke all on schema market from public, anon, authenticated;
revoke all on all tables in schema market from public, anon, authenticated;
