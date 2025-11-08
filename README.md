# FinanceSum - Financial Analysis Platform

A comprehensive web application that fetches, analyzes, and summarizes quarterly/annual filings for any public company, providing investor-grade analysis through the lens of 10 famous investors.

## Features

- **Comprehensive Financial Data**: Fetch structured financial data via EODHD API for any public company
- **Historical Data**: Access 35+ years of financial history (US companies from 1985+)
- **Automated Analysis**: Extract and normalize income statements, balance sheets, and cash flow statements
- **Financial Ratios**: Calculate 16+ financial ratios automatically
- **Health Scoring**: Composite health score (0-100) with weighted components
- **AI-Powered Summaries**: Generate investment memos using Gemini AI
- **Investor Personas**: Simulate how 10 famous investors would view the company
- **Compare Mode**: Side-by-side comparison of multiple companies
- **Export**: Download analysis as PDF/Word/Markdown

## Architecture

- **Frontend**: Next.js 14, TypeScript, Tailwind CSS
- **Backend**: FastAPI (Python), Celery for background tasks
- **Database**: Supabase (PostgreSQL + Storage)
- **AI**: Google Gemini 2.5 Flash Lite
- **Queue**: Redis

## Getting Started

### Prerequisites

- Node.js 18+ and npm/yarn
- Python 3.11+
- Docker and Docker Compose
- Supabase account

### Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Financesum
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your Supabase and Gemini API credentials
   ```

   Update `frontend/.env.local` as well. By default the app expects real Supabase OAuth (`NEXT_PUBLIC_AUTH_MODE=supabase`).  
   - Set `NEXT_PUBLIC_ALLOW_DEMO_FALLBACK=true` (default) if you’d like the UI to automatically fall back to a local demo session whenever Google sign-in isn’t configured yet.  
   - To opt into demo-only mode, set `NEXT_PUBLIC_AUTH_MODE=demo`.  
   Remember to configure the Google provider inside Supabase Auth and whitelist your site URL in the redirect list before using the real flow.

3. **Start everything with one command (no Docker)**
   ```bash
   # From the repository root
   python3 run_dev.py
   ```

   The script will ensure dependencies are installed (Python venv + npm packages), start `redis-server`, launch the FastAPI backend, Celery worker, and finally the Next.js dev server at http://localhost:3000. Press Ctrl+C to stop everything.

   Requirements: Python 3.11+, Node.js 18+, and `redis-server` available on your PATH (e.g. via `brew install redis`).

4. **Start using Docker (alternative)**
   ```bash
   chmod +x start-dev.sh   # only needed once
   ./start-dev.sh          # or: bash start-dev.sh
   ```

   This uses Docker Compose to run Redis, the backend, and Celery in containers, then starts the Next.js dev server locally. When you stop the script (Ctrl+C) it automatically shuts everything down.

5. **Manual setup (if you prefer to run services individually)**
5. **Manual setup (if you prefer to run services individually)**

   - Start backend services
     ```bash
     docker-compose up -d
     ```

   - Set up frontend
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

   - Set up backend
   ```bash
   cd backend
   pip install -r requirements.txt
   python -m app.main
   ```

   - Run Celery workers
   ```bash
   cd backend
   celery -A app.tasks.celery_app worker --loglevel=info
   ```

## Project Structure

```
financesum/
├── frontend/          # Next.js application
├── backend/           # FastAPI application
├── supabase/          # Database migrations
└── docker-compose.yml # Local development setup
```

## Database Schema

Run migrations in Supabase to create:
- `companies` - Company information
- `filings` - Filing metadata
- `financial_statements` - Parsed financial data
- `analyses` - Analysis results with health scores
- `watchlists` - User watchlists

## API Endpoints

- `POST /api/v1/companies/lookup` - Search for companies
- `POST /api/v1/filings/fetch` - Fetch company filings
- `POST /api/v1/analysis/run` - Run financial analysis
- `GET /api/v1/analysis/{id}` - Get analysis results
- `POST /api/v1/analysis/{id}/personas` - Generate persona views

## Investor Personas

The platform simulates 10 famous investors:
1. Warren Buffett
2. Charlie Munger
3. Benjamin Graham
4. Peter Lynch
5. Ray Dalio
6. Cathie Wood
7. Joel Greenblatt
8. John Bogle
9. Howard Marks
10. Bill Ackman

**Disclaimer**: All persona outputs are simulations based on publicly available writings and investment philosophies. They do not represent actual advice from these investors.

## Development

### Running Tests

**Backend**:
```bash
cd backend
pytest
```

**Frontend**:
```bash
cd frontend
npm test
```

### Code Quality

```bash
# Backend linting
cd backend
black . && flake8 .

# Frontend linting
cd frontend
npm run lint
```

## License

MIT License

## Contributing

Contributions are welcome! Please read our contributing guidelines before submitting PRs.
