-- Migration: add user_id to summary events for usage limits

ALTER TABLE public.filing_summary_events
  ADD COLUMN IF NOT EXISTS user_id TEXT;

CREATE INDEX IF NOT EXISTS idx_filing_summary_events_user_id
  ON public.filing_summary_events (user_id);

CREATE INDEX IF NOT EXISTS idx_filing_summary_events_user_id_created_at
  ON public.filing_summary_events (user_id, created_at DESC);

COMMENT ON COLUMN public.filing_summary_events.user_id IS
  'Optional auth user id associated with the summary generation event.';
