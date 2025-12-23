-- Migration: scope analyses to individual users

ALTER TABLE public.analyses
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_analyses_user_id
  ON public.analyses (user_id);

DROP POLICY IF EXISTS "Analyses are viewable by everyone" ON public.analyses;
DROP POLICY IF EXISTS "Analyses are insertable by authenticated users" ON public.analyses;

CREATE POLICY "Users can view their own analyses" ON public.analyses
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own analyses" ON public.analyses
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own analyses" ON public.analyses
  FOR DELETE USING (auth.uid() = user_id);
