-- Migration: Summary generation events tracking
-- Purpose:
--   Persist a durable, server-side record of every filing summary generation attempt.
--   Dashboard can then show all-time totals and per-day activity even if users remove
--   summaries from local dashboard history.
--
-- Notes:
--   - This table is append-only from the app's perspective.
--   - We keep the schema minimal; if later you add auth/user_id you can extend.
--   - Uses timestamptz for correct date bucketing.

create extension if not exists pgcrypto;

create table if not exists public.filing_summary_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),

  -- The filing we generated a summary for (matches backend filing_id string/UUID)
  filing_id text not null,

  -- Optional: for easier joins/filters later. Stored as text to avoid coupling to UUID validity in fallback mode.
  company_id text null,

  -- Optional metadata for analytics/debugging (no PII).
  mode text null,          -- "default" / "custom"
  cached boolean null,     -- whether response was served from cache (currently cache disabled)
  source text null         -- "supabase" / "fallback"
);

create index if not exists idx_filing_summary_events_created_at
  on public.filing_summary_events (created_at desc);

create index if not exists idx_filing_summary_events_filing_id
  on public.filing_summary_events (filing_id);

create index if not exists idx_filing_summary_events_company_id
  on public.filing_summary_events (company_id);

comment on table public.filing_summary_events is
  'Append-only event log of each time a filing summary is generated. Used for dashboard totals/activity.';

comment on column public.filing_summary_events.filing_id is
  'ID of filing summarized. Stored as text for compatibility with UUID and non-UUID fallback IDs.';

comment on column public.filing_summary_events.company_id is
  'Optional company id associated with the filing (text for compatibility).';