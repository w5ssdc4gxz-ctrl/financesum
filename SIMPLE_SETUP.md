# FinanceSum - Simple Setup

## What You Need

Only **3 services** and **6 environment variables**:

### Services:
1. **Supabase** - Database & Storage (free tier)
2. **OpenAI** - AI analysis
3. **EODHD** - Financial data (demo key or free tier)

### Environment Variables:

```bash
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
OPENAI_API_KEY=...
EODHD_API_KEY=demo
NEXT_PUBLIC_SUPABASE_URL=... (same as SUPABASE_URL)
NEXT_PUBLIC_SUPABASE_ANON_KEY=... (same as SUPABASE_ANON_KEY)
```

## Minimal .env File

```bash
# Get these from https://app.supabase.com
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Get from OpenAI
OPENAI_API_KEY=sk-...

# Use "demo" or get from https://eodhd.com
EODHD_API_KEY=demo

# For frontend (same as above)
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## Run Commands

```bash
# 1. Start Redis
docker run -p 6379:6379 redis:7-alpine

# 2. Start Backend (new terminal)
cd backend && uvicorn app.main:app --reload

# 3. Start Celery Worker (new terminal)
cd backend && celery -A app.tasks.celery_app worker --loglevel=info

# 4. Start Frontend (new terminal)
cd frontend && npm run dev
```

## Done!

Open http://localhost:3000 and search for **AAPL**

## Why So Simple?

- ❌ **No SEC EDGAR parsing** - We use EODHD API for structured data
- ❌ **No PDF downloads** - EODHD returns clean JSON
- ❌ **No complex table extraction** - Already parsed
- ❌ **No OCR needed** - Data is text, not images
- ✅ **Just API calls** - Fast and reliable

## What EODHD Gives Us

One API call to `https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo` returns:

- 35+ years of quarterly data
- 35+ years of annual data
- Income statements
- Balance sheets
- Cash flow statements
- Company info
- All pre-parsed and normalized

## Cost

**Development/Testing (FREE):**
- Supabase: Free tier
- OpenAI: Pay-per-use
- EODHD: Demo key (for AAPL, TSLA, AMZN, VTI)

**Production (Cheap):**
- Supabase: Free tier or $25/month
- OpenAI: Pay-per-use
- EODHD: $19.99/month (unlimited calls, all stocks)

**Total: ~$45/month for full production**

## Architecture

```
User searches AAPL
    ↓
EODHD API returns 35 years of data (JSON)
    ↓
Store in Supabase (already parsed!)
    ↓
Calculate ratios
    ↓
OpenAI generates analysis
    ↓
Show results
```

No PDF parsing, no OCR, no headaches! 🎉















