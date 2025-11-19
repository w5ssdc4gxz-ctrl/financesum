# FinanceSum Setup Guide

This guide will walk you through setting up the FinanceSum financial analysis platform.

## Prerequisites

- Node.js 18+ and npm/yarn
- Python 3.11+
- Docker and Docker Compose
- Supabase account (or self-hosted Supabase)
- Google Gemini API key

## Quick Start

### 1. Environment Setup

Create a `.env` file in the root directory with your credentials:

```bash
# Supabase Configuration
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key

# Backend API
NEXT_PUBLIC_API_URL=http://localhost:8000

# Redis
REDIS_URL=redis://localhost:6379/0

# SEC EDGAR
EDGAR_USER_AGENT="YourCompany contact@yourdomain.com"
```

Also create `frontend/.env.local`:

```bash
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 2. Database Setup

Run the Supabase migrations to create the database schema:

1. Go to your Supabase project dashboard
2. Navigate to SQL Editor
3. Run the migrations in order:
   - `supabase/migrations/001_initial_schema.sql`
   - `supabase/migrations/002_add_storage_policies.sql`

4. Create the `filings` storage bucket:
   - Go to Storage in Supabase dashboard
   - Create a new bucket named `filings`
   - Set it to public

5. Enable Google OAuth:
   - Go to Authentication > Providers
   - Enable Google provider
   - Add your OAuth credentials

### 3. Backend Setup

```bash
cd backend

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests to verify setup
pytest

# Start the backend API (development)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Start Background Services with Docker

```bash
# From the root directory
docker-compose up -d

# This starts:
# - Redis (for Celery)
# - Backend API
# - Celery worker
```

### 5. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

The application will be available at `http://localhost:3000`

## Development Workflow

### Running the Full Stack

**Option 1: Using Docker Compose (Recommended)**

```bash
# Start all services
docker-compose up

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

**Option 2: Manual Start**

Terminal 1 - Redis:
```bash
redis-server
```

Terminal 2 - Backend:
```bash
cd backend
uvicorn app.main:app --reload
```

Terminal 3 - Celery Worker:
```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info
```

Terminal 4 - Frontend:
```bash
cd frontend
npm run dev
```

### Running Tests

**Backend Tests:**
```bash
cd backend
pytest
pytest --cov=app  # With coverage
```

**Frontend Tests:**
```bash
cd frontend
npm test
```

## Usage

### Basic Workflow

1. **Search for a Company**
   - Go to Dashboard
   - Enter ticker symbol (e.g., "AAPL") or company name
   - Select the company from results

2. **Fetch Filings**
   - On the company page, click "Fetch Latest Filings"
   - This downloads 10-K and 10-Q filings from SEC EDGAR
   - Wait a few minutes for the background task to complete

3. **Run Analysis**
   - Once filings are fetched, click "Run Analysis"
   - Select which investor personas to include (optional)
   - The analysis will:
     - Parse financial statements
     - Calculate 16+ financial ratios
     - Compute health score
     - Generate AI summary
     - Create persona-specific views

4. **View Results**
   - Navigate through tabs: Overview, Filings, Analysis, Investor Personas
   - View financial charts and metrics
   - Read AI-generated investment analysis
   - See how different famous investors would view the company

5. **Compare Companies**
   - Go to Compare page
   - Search and select up to 4 companies
   - Generate side-by-side comparison

## API Endpoints

### Companies
- `POST /api/v1/companies/lookup` - Search companies
- `GET /api/v1/companies/{id}` - Get company details

### Filings
- `POST /api/v1/filings/fetch` - Fetch filings from SEC
- `GET /api/v1/filings/company/{company_id}` - List company filings
- `POST /api/v1/filings/{id}/parse` - Parse a filing

### Analysis
- `POST /api/v1/analysis/run` - Run financial analysis
- `GET /api/v1/analysis/{id}` - Get analysis results
- `GET /api/v1/analysis/task/{task_id}` - Check task status

## Troubleshooting

### Backend Issues

**Import errors:**
```bash
# Make sure you're in the backend directory and have installed all dependencies
pip install -r requirements.txt
```

**Celery not processing tasks:**
```bash
# Check Redis is running
redis-cli ping

# Check Celery worker logs
celery -A app.tasks.celery_app worker --loglevel=debug
```

**Supabase connection errors:**
- Verify your Supabase URL and keys in `.env`
- Check that migrations have been run
- Ensure RLS policies are set correctly

### Frontend Issues

**Build errors:**
```bash
# Clear Next.js cache
rm -rf frontend/.next
cd frontend && npm install
```

**Authentication not working:**
- Verify Supabase credentials in `frontend/.env.local`
- Check that Google OAuth is enabled in Supabase
- Ensure redirect URL is configured: `http://localhost:3000/auth/callback`

### SEC EDGAR Issues

**403 Forbidden errors:**
- Update `EDGAR_USER_AGENT` in `.env` with valid contact info
- SEC requires a proper User-Agent header

**Rate limiting:**
- SEC allows 10 requests per second
- Our implementation respects this limit

## Production Deployment

### Environment Variables

Set all production environment variables:
- Use production Supabase URL and keys
- Use production Gemini API key
- Set proper CORS origins
- Configure production Redis URL

### Build Frontend

```bash
cd frontend
npm run build
npm start
```

### Deploy Backend

```bash
cd backend
# Use a production ASGI server
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

### Scaling Considerations

- Use managed Redis (AWS ElastiCache, Redis Cloud)
- Scale Celery workers horizontally
- Use CDN for frontend static assets
- Consider caching frequently accessed analyses
- Monitor API rate limits for SEC EDGAR and Gemini

## Security Notes

- Never commit `.env` files
- Use environment-specific Supabase keys
- Enable RLS policies in production
- Validate all user inputs
- Rate limit API endpoints
- Regularly rotate API keys

## Support

For issues or questions:
- Check the README.md
- Review error logs in backend and Celery
- Check Supabase logs
- Verify all environment variables are set

## License

MIT License - See LICENSE file for details
















