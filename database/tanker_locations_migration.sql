-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- Stores the LATEST known GPS location for each tanker operator.
-- One row per tanker_operator_id (upserted on every ping from the browser).

create table if not exists public.tanker_locations (
    tanker_operator_id text primary key,
    user_id text,
    tanker_operator_name text,
    latitude double precision not null,
    longitude double precision not null,
    accuracy double precision,
    speed double precision,
    heading double precision,
    recorded_at timestamptz,
    updated_at timestamptz default now()
);

-- Keep lookups by operator fast (primary key already covers this, index kept
-- here in case the table is later changed to a history table instead).
create index if not exists idx_tanker_locations_operator
    on public.tanker_locations (tanker_operator_id);

-- Row Level Security.
-- IMPORTANT: this project's Flask backend connects to Supabase with the
-- PUBLISHABLE/ANON key (see .env / SUPABASE_KEY), not the service_role key,
-- and access control is already enforced in Flask via @login_required
-- (only a logged-in tanker operator can call /api/tanker/location).
-- So RLS is enabled here, but with policies that allow the anon role to
-- read/write -- the same trust model already used by the rest of this app.
alter table public.tanker_locations enable row level security;

drop policy if exists "tanker_locations_anon_all" on public.tanker_locations;

create policy "tanker_locations_anon_all"
    on public.tanker_locations
    for all
    to anon
    using (true)
    with check (true);
