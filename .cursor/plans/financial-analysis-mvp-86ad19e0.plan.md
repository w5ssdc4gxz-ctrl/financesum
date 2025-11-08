<!-- 86ad19e0-a6ad-4f14-b904-444f92061b37 cb25dd1c-d31b-424c-b8a8-7a9f25b57920 -->
# Financial Analysis Platform - Full MVP Implementation

## Architecture Overview

- **Frontend**: Next.js 14+ (App Router), TypeScript, Tailwind CSS, React
- **Backend**: FastAPI (Python) with Celery for background tasks
- **Database**: Supabase PostgreSQL + Supabase Storage for PDFs
- **AI**: Gemini 2.5 Flash Lite (via API key in Supabase)
- **Key Libraries**: PyMuPDF, Camelot/Tabula (PDF parsing), SEC EDGAR API

## Phase 1: Project Setup & Infrastructure

### Frontend Setup (Next.js)

- Initialize Next.js 14 with TypeScript and Tailwind CSS
- Configure Supabase client (@supabase/supabase-js) with .env variables
- Set up authentication with Supabase Auth (Google OAuth)
- Create base layout with navigation and auth state management
- Configure API proxy to backend FastAPI service

### Backend Setup (FastAPI)

- Create FastAPI project structure with Poetry/pip
- Set up Supabase Python client for database access
- Configure CORS for Next.js frontend
- Create Celery worker configuration with Redis
- Set up environment variables (.env) for Supabase and Gemini API key
- Create Docker Compose for local development (FastAPI, Redis, workers)

### Database Schema (Supabase)

Create PostgreSQL tables via migrations:

- `companies` (id, ticker, cik, name, exchange, industry, sector, country)
- `filings` (id, company_id, filing_type, filing_date, url, raw_file_path, parsed_json_path, pages)
- `financial_statements` (id, filing_id, period_start, period_end, currency, statements jsonb)
- `analyses` (id, company_id, filing_ids[], analysis_date, health_score, ratios jsonb, summary_md, investor_persona_summaries jsonb, provenance jsonb)
- `users` (extended from Supabase auth)
- `watchlists` (user_id, company_id)

## Phase 2: SEC EDGAR Filing Fetcher

### Backend API Endpoints

- `POST /api/v1/companies/lookup` - Search company by ticker/CIK/name using SEC EDGAR API
- `POST /api/v1/filings/fetch` - Fetch available filings for a company
- Background task to download PDF/HTML from EDGAR and store in Supabase Storage

### Implementation Details

- Use `sec-edgar-downloader` or manual EDGAR API integration
- Parse EDGAR RSS/index files for filing metadata
- Download and store raw files in Supabase Storage (path: `filings/{company_id}/{filing_id}.pdf`)
- Store filing metadata in `filings` table

## Phase 3: PDF/HTML Parsing Pipeline

### Document Ingestion Service

- PDF parser using PyMuPDF + fallback OCR with Tesseract
- HTML parser for EDGAR HTML filings
- Structural detection: identify MD&A, Risk Factors, Financial Statements sections

### Table Extraction

- Use Camelot/Tabula for table extraction from PDFs
- Custom heuristics for multi-column financial tables
- Map extracted line items to canonical taxonomy (Revenue, Gross Profit, Net Income, etc.)
- Capture footnote references

### Normalization

- Detect units (thousands/millions) and convert to numeric
- Parse period dates (fiscal quarters/years)
- Reconciliation checks (Assets = Liabilities + Equity)
- Store parsed JSON in `parsed_json_path` and structured data in `financial_statements` table

## Phase 4: Financial Analysis Engine

### Ratio Calculation Service

Implement all specified ratios with exact formulas:

- **Profitability**: Revenue Growth YoY, Gross/Operating/Net Margin, ROA, ROE
- **Liquidity**: Current Ratio, Quick Ratio, DSO, Inventory Turnover
- **Leverage**: Debt-to-Equity, Net Debt/EBITDA, Interest Coverage
- **Cash Flow**: FCF, FCF Margin
- **Distress**: Altman Z-Score (when applicable)

### Health Score Computation

- Compute percentile ranks vs industry peers for each ratio
- Weighted composite score (Financial Performance 35%, Profitability 20%, Leverage 15%, Liquidity 10%, Cash Flow 10%, Governance 5%, Growth 5%)
- Score bands: 0-49 (At Risk), 50-69 (Watch), 70-84 (Healthy), 85-100 (Very Healthy)
- Store in `analyses` table with provenance

### API Endpoints

- `POST /api/v1/analysis/run` - Trigger analysis (returns task ID)
- `GET /api/v1/analysis/{analysis_id}` - Get analysis results
- `GET /api/v1/analysis/{analysis_id}/status` - Check background task status

## Phase 5: AI Analysis with Gemini

### NLP/AI Service

- Configure Gemini 2.5 Flash Lite client with API key from Supabase
- Extractive summarization: identify key sentences from MD&A/Risk Factors
- Abstractive summarization: generate investor memo using Gemini

### Prompt Templates

Implement prompts for:

1. **General Summary**: TL;DR (3 sentences), investment thesis (5 bullets), top 5 risks, 3 catalysts, 5 KPIs to monitor
2. **Citations**: Include filing references like `[10-K:page 12]`
3. Store summary in `analyses.summary_md`

### API Integration

- `POST /api/v1/analysis/{analysis_id}/generate-summary` - Generate AI summary
- Include financial data + extracted text passages in prompt context
- Handle rate limiting and retries

## Phase 6: Investor Persona Simulation

### Persona Configuration

Create persona definitions (10 investors):

1. Warren Buffett - value, moat, FCF, conservative leverage
2. Charlie Munger - rational, quality businesses
3. Benjamin Graham - margin of safety, low P/E
4. Peter Lynch - GARP, understandable businesses
5. Ray Dalio - macro-aware, balance sheet focus
6. Cathie Wood - disruptive innovation, growth
7. Joel Greenblatt - magic formula, ROC
8. John Bogle - index investor, low-cost
9. Howard Marks - cycles, risk assessment
10. Bill Ackman - activist, catalysts

Each persona includes:

- Philosophy (2 sentences)
- Priority checklist (3 items)
- Tone descriptor

### Persona Transform Service

- `POST /api/v1/analysis/{analysis_id}/personas` - Generate persona views
- Use Gemini with persona-specific prompts
- Transform general memo into persona-specific view
- Include simulated stance (Buy/Hold/Sell) with brief reasoning
- Store in `analyses.investor_persona_summaries` JSONB
- Display legal disclaimer: "Simulated view based on public writings, not real advice"

## Phase 7: Frontend UI

### Pages & Components

1. **Landing Page** (`/`) - Value prop, demo, CTA
2. **Dashboard** (`/dashboard`) - Watchlist, recent analyses, search
3. **Company Page** (`/company/[id]`) - Tabs: Overview, Filings, Financials, Analysis, Personas, Compare
4. **Document Viewer** (`/filings/[id]`) - PDF viewer with AI highlights
5. **Compare** (`/compare`) - Side-by-side comparison (up to 4 companies)
6. **Export Modal** - Download as PDF/Word/Markdown

### Key Features

- Company search with autocomplete
- Filing list with fetch button
- Financial charts (Recharts) - ratios over time
- Health score visualization with color-coded bands
- Persona selector (checkboxes for 10 personas)
- Export functionality
- Responsive design with Tailwind

### API Integration

- Use React Query (TanStack Query) for data fetching and caching
- WebSocket connection for real-time progress updates on parsing/analysis
- Error handling and loading states

## Phase 8: Background Workers & Job Queue

### Celery Tasks

- `fetch_filings_task` - Download from EDGAR
- `parse_document_task` - PDF/HTML parsing
- `analyze_company_task` - Compute ratios and health score
- `generate_summary_task` - AI summary generation
- `generate_personas_task` - All persona views

### Progress Tracking

- Store task status in database or Redis
- WebSocket notifications to frontend
- Retry logic with exponential backoff

## Phase 9: Testing & Edge Cases

### Unit Tests

- Ratio calculation functions with known inputs
- Table mapping and normalization logic
- Health score computation

### Integration Tests

- End-to-end filing fetch → parse → analyze flow
- Golden-file tests with real SEC filings
- API endpoint tests

### Edge Case Handling

- Multi-column/multi-segment tables
- Restatements and non-GAAP measures (label clearly)
- Currency conversions (store currency metadata)
- OCR fallback for scanned PDFs
- Missing data gracefully handled

## Phase 10: Polish & Production Readiness

### Security & Compliance

- HTTPS/TLS everywhere
- Row-level security (RLS) in Supabase for user data
- Rate limiting on API endpoints
- GDPR/CCPA data export and deletion
- Privacy policy and terms of service pages

### Performance Optimization

- Redis caching for frequently accessed analyses
- Lazy loading for large PDF documents
- Pagination for filing lists and search results
- Database indexes on frequently queried columns

### Monitoring & Logging

- Structured logging in FastAPI
- Error tracking (Sentry or similar)
- Basic analytics (Plausible or PostHog)

## File Structure

```
financesum/
├── frontend/                    # Next.js app
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── login/
│   │   │   └── signup/
│   │   ├── dashboard/
│   │   ├── company/[id]/
│   │   ├── compare/
│   │   └── layout.tsx
│   ├── components/
│   │   ├── CompanySearch.tsx
│   │   ├── FilingsList.tsx
│   │   ├── FinancialCharts.tsx
│   │   ├── HealthScoreBadge.tsx
│   │   ├── PersonaSelector.tsx
│   │   └── ...
│   ├── lib/
│   │   ├── supabase.ts
│   │   └── api-client.ts
│   └── package.json
│
├── backend/                     # FastAPI app
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── companies.py
│   │   │   ├── filings.py
│   │   │   └── analysis.py
│   │   ├── services/
│   │   │   ├── edgar_fetcher.py
│   │   │   ├── pdf_parser.py
│   │   │   ├── table_extractor.py
│   │   │   ├── ratio_calculator.py
│   │   │   ├── health_scorer.py
│   │   │   ├── gemini_client.py
│   │   │   └── persona_engine.py
│   │   ├── models/
│   │   │   ├── database.py
│   │   │   └── schemas.py
│   │   ├── tasks/              # Celery tasks
│   │   │   ├── fetch.py
│   │   │   ├── parse.py
│   │   │   └── analyze.py
│   │   └── config.py
│   ├── requirements.txt
│   └── Dockerfile
│
├── supabase/
│   └── migrations/
│       ├── 001_initial_schema.sql
│       └── 002_add_watchlists.sql
│
├── docker-compose.yml
├── .env.example
└── README.md
```

## Key Implementation Notes

- **Supabase Integration**: Use environment variables for `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
- **Gemini API**: Retrieve API key from Supabase (stored as secret) in backend
- **EDGAR API**: Respect rate limits (10 requests/second with User-Agent header)
- **PDF Storage**: Use Supabase Storage buckets with signed URLs for secure access
- **Persona Disclaimer**: Display prominently on all persona outputs
- **Provenance**: Always store which filings/pages were used for each analysis

### To-dos

- [x] Set up Next.js frontend, FastAPI backend, Docker Compose, and Supabase database schema