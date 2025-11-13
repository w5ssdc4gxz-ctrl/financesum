-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Companies table
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker TEXT NOT NULL,
    cik TEXT,
    name TEXT NOT NULL,
    exchange TEXT,
    industry TEXT,
    sector TEXT,
    country TEXT DEFAULT 'US',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(ticker, exchange)
);

CREATE INDEX idx_companies_ticker ON companies(ticker);
CREATE INDEX idx_companies_cik ON companies(cik);
CREATE INDEX idx_companies_name ON companies(name);

-- Filings table
CREATE TABLE filings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filing_type TEXT NOT NULL, -- 10-K, 10-Q, 8-K, etc.
    filing_date DATE NOT NULL,
    period_end DATE,
    url TEXT,
    raw_file_path TEXT, -- Path in Supabase Storage
    parsed_json_path TEXT, -- Path in Supabase Storage
    pages INTEGER,
    status TEXT DEFAULT 'pending', -- pending, processing, parsed, failed
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_filings_company_id ON filings(company_id);
CREATE INDEX idx_filings_filing_type ON filings(filing_type);
CREATE INDEX idx_filings_filing_date ON filings(filing_date);
CREATE INDEX idx_filings_status ON filings(status);

-- Financial statements table
CREATE TABLE financial_statements (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    currency TEXT DEFAULT 'USD',
    statements JSONB NOT NULL, -- Normalized financial data
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_financial_statements_filing_id ON financial_statements(filing_id);
CREATE INDEX idx_financial_statements_period ON financial_statements(period_start, period_end);

-- Analyses table
CREATE TABLE analyses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filing_ids UUID[] NOT NULL,
    analysis_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    health_score NUMERIC(5,2),
    score_band TEXT, -- At Risk, Watch, Healthy, Very Healthy
    ratios JSONB,
    summary_md TEXT,
    investor_persona_summaries JSONB, -- Keyed by persona ID
    provenance JSONB, -- Which filings/pages used
    status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_analyses_company_id ON analyses(company_id);
CREATE INDEX idx_analyses_analysis_date ON analyses(analysis_date);
CREATE INDEX idx_analyses_status ON analyses(status);

-- Watchlists table
CREATE TABLE watchlists (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, company_id)
);

CREATE INDEX idx_watchlists_user_id ON watchlists(user_id);
CREATE INDEX idx_watchlists_company_id ON watchlists(company_id);

-- User profiles table (extends Supabase auth.users)
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name TEXT,
    avatar_url TEXT,
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Task status table (for Celery task tracking)
CREATE TABLE task_status (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id TEXT UNIQUE NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL, -- pending, running, completed, failed
    progress INTEGER DEFAULT 0,
    result JSONB,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_task_status_task_id ON task_status(task_id);
CREATE INDEX idx_task_status_status ON task_status(status);

-- Enable Row Level Security
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_status ENABLE ROW LEVEL SECURITY;

-- RLS Policies for companies (public read)
CREATE POLICY "Companies are viewable by everyone" ON companies
    FOR SELECT USING (true);

CREATE POLICY "Companies are insertable by authenticated users" ON companies
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- RLS Policies for filings (public read)
CREATE POLICY "Filings are viewable by everyone" ON filings
    FOR SELECT USING (true);

CREATE POLICY "Filings are insertable by authenticated users" ON filings
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- RLS Policies for financial_statements (public read)
CREATE POLICY "Financial statements are viewable by everyone" ON financial_statements
    FOR SELECT USING (true);

CREATE POLICY "Financial statements are insertable by authenticated users" ON financial_statements
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- RLS Policies for analyses (public read)
CREATE POLICY "Analyses are viewable by everyone" ON analyses
    FOR SELECT USING (true);

CREATE POLICY "Analyses are insertable by authenticated users" ON analyses
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- RLS Policies for watchlists (user-specific)
CREATE POLICY "Users can view their own watchlists" ON watchlists
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can create their own watchlists" ON watchlists
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own watchlists" ON watchlists
    FOR DELETE USING (auth.uid() = user_id);

-- RLS Policies for user_profiles
CREATE POLICY "Users can view their own profile" ON user_profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update their own profile" ON user_profiles
    FOR UPDATE USING (auth.uid() = id);

CREATE POLICY "Users can insert their own profile" ON user_profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- RLS Policies for task_status (authenticated users)
CREATE POLICY "Task status viewable by authenticated users" ON task_status
    FOR SELECT USING (auth.role() = 'authenticated');

-- Functions for automatic updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
CREATE TRIGGER update_companies_updated_at BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_filings_updated_at BEFORE UPDATE ON filings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_financial_statements_updated_at BEFORE UPDATE ON financial_statements
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_analyses_updated_at BEFORE UPDATE ON analyses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_task_status_updated_at BEFORE UPDATE ON task_status
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Storage buckets (to be created via Supabase UI or API)
-- Bucket: filings (for raw PDFs/HTML and parsed JSON)
-- Policies: authenticated users can upload, everyone can read












