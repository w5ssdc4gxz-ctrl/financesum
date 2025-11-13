# How to Run FinanceSum - Simplified Guide

## Prerequisites

- Node.js 18+
- Python 3.11+
- Docker (for Redis)
- Supabase account (free)
- Gemini API key (free)
- EODHD API key (use "demo" for testing)

## Step 1: Set Up Environment Variables

Create `.env` in the project root:

```bash
# Supabase (get from https://app.supabase.com)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJhbGc...your_anon_key
SUPABASE_SERVICE_ROLE_KEY=eyJhbGc...your_service_key

# Gemini AI (get from https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=AIzaSy...your_gemini_key

# EODHD API (use "demo" for testing, or get from https://eodhd.com)
EODHD_API_KEY=demo

# Frontend
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGc...your_anon_key
```

**That's it! Only 6 variables needed.**

## Step 2: Set Up Database

1. Go to https://app.supabase.com
2. Open SQL Editor
3. Run `supabase/migrations/001_initial_schema.sql`
4. Run `supabase/migrations/002_add_storage_policies.sql`
5. Go to Storage → Create bucket "filings" (make it public)

## Step 3: Install Dependencies

```bash
# Backend
cd backend
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

## Step 4: Start Everything

**Terminal 1 - Redis (using Docker):**
```bash
docker run -p 6379:6379 redis:7-alpine
```

**Terminal 2 - Backend:**
```bash
cd backend
uvicorn app.main:app --reload
```

**Terminal 3 - Celery Worker:**
```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info
```

**Terminal 4 - Frontend:**
```bash
cd frontend
npm run dev
```

## Step 5: Open App

Go to **http://localhost:3000**

## Test It

1. Search for **AAPL** (works with demo key)
2. Click **"Fetch Latest Filings"** (~5 seconds)
3. Click **"Run Analysis"** (~30 seconds)
4. View results in the tabs!

## With Demo Key

The `demo` key works with:
- AAPL (Apple)
- TSLA (Tesla)
- AMZN (Amazon)
- VTI (Vanguard ETF)

For other companies, sign up at https://eodhd.com (free tier: 20 calls/day)

## Troubleshooting

**Can't connect to backend?**
- Make sure port 8000 isn't in use
- Check `http://localhost:8000/health` shows `{"status":"healthy"}`

**Analysis not working?**
- Verify Celery worker is running (Terminal 3)
- Check Gemini API key is valid
- Verify Redis is running (Terminal 1)

**Frontend errors?**
- Delete `.next` folder: `rm -rf frontend/.next`
- Reinstall: `cd frontend && npm install`

**Database errors?**
- Check migrations ran successfully
- Verify Supabase URL and keys are correct
- Make sure "filings" bucket exists

## That's It!

No EDGAR configuration needed - we only use EODHD API for all financial data.

Clean, simple, fast! 🚀












