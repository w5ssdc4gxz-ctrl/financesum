# Quick Start Guide

Get FinanceSum running in 5 minutes!

## Prerequisites

Make sure you have:
- ✅ Node.js 18+ installed
- ✅ Python 3.11+ installed  
- ✅ Docker and Docker Compose installed
- ✅ A Supabase account (free tier is fine)
- ✅ A Google Gemini API key (free tier available)
- ✅ An EODHD API key ([Get it here](https://eodhd.com/) - free tier available with demo key)

## Step 1: Clone and Setup Environment

```bash
cd /Users/alexandersibast/Documents/Financesum

# Copy environment templates
cp .env.example .env
cp frontend/.env.local.example frontend/.env.local
```

## Step 2: Configure Your Credentials

Edit `.env` and add:
- Your Supabase URL and keys (from Supabase dashboard)
- Your Gemini API key (from Google AI Studio)
- Your EODHD API key (from https://eodhd.com/ - you can use "demo" for testing AAPL, TSLA, etc.)
- Your email for SEC EDGAR User-Agent (optional, used for fallback)

Edit `frontend/.env.local` and add:
- Same Supabase URL and anon key

## Step 3: Setup Database

1. Go to your Supabase dashboard → SQL Editor
2. Run the migration files:
   - Copy/paste `supabase/migrations/001_initial_schema.sql` and execute
   - Copy/paste `supabase/migrations/002_add_storage_policies.sql` and execute

3. Create storage bucket:
   - Go to Storage → Create bucket → Name it `filings` → Make it public

4. Enable Google OAuth:
   - Go to Authentication → Providers → Enable Google
   - Follow Supabase's guide to set up OAuth

## Step 4: Install Dependencies

```bash
# Backend
cd backend
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

## Step 5: Start Everything

### Option A: Using the start script (easiest)

```bash
chmod +x start-dev.sh
./start-dev.sh
```

### Option B: Manual start

```bash
# Terminal 1: Start Docker services
docker-compose up

# Terminal 2: Start frontend
cd frontend
npm run dev
```

## Step 6: Open the App

Go to http://localhost:3000

🎉 **You're ready!**

## First Time Usage

1. Click "Sign In with Google" (or just browse without signing in)
2. Search for a company (try "AAPL" or "TSLA")
3. Click "Fetch Latest Filings" (wait ~2-3 minutes)
4. Click "Run Analysis" (wait ~3-5 minutes for AI to process)
5. Explore the tabs: Overview, Filings, Analysis, and Investor Personas!

## Troubleshooting

**Backend won't start?**
- Check that all .env variables are set
- Make sure port 8000 is not in use
- Check Docker logs: `docker-compose logs backend`

**Frontend build errors?**
- Delete `.next` folder and node_modules
- Run `npm install` again

**Database errors?**
- Verify migrations were run successfully
- Check Supabase dashboard for connection issues
- Ensure RLS policies are in place

**Authentication not working?**
- Verify Google OAuth is enabled in Supabase
- Check that redirect URL is set to `http://localhost:3000/auth/callback`

## Next Steps

- Read `SETUP.md` for detailed configuration
- Read `IMPLEMENTATION_SUMMARY.md` for technical details
- Check `README.md` for full documentation

## Need Help?

Common issues and solutions are in `SETUP.md` under "Troubleshooting"

