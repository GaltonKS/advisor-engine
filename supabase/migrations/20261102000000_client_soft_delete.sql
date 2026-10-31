-- Renaming and deleting clients.
--
-- Deleting a client is a soft delete: the row gets `deleted_at` / `deleted_by` and the
-- API stops listing or serving it, along with its portfolios, drafts and conversations.
-- Nothing is removed, so every past analysis and approved draft stays on record next to
-- the audit trail (which is append-only and refers to these ids).
--
-- Renaming is a plain UPDATE of `name`; both are audited by the API.

alter table public.clients
    add column deleted_at timestamptz,
    add column deleted_by uuid references public.advisers (id);

create index on public.clients (firm_id) where deleted_at is null;

-- clients already allow UPDATE for the caller's firm (policy firm_rows), and DELETE is
-- deliberately not granted to `authenticated`; no grant change.
