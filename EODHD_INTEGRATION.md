# EODHD API Integration Guide

FinanceSum uses the [EODHD Fundamentals API](https://eodhd.com/) to fetch comprehensive financial data for public companies.

## Why EODHD?

Instead of parsing raw PDF filings from SEC EDGAR, we use EODHD's structured API which provides:

✅ **Pre-parsed financial data** - No PDF extraction needed
✅ **35+ years of history** - US companies from 1985, international from 2000
✅ **Quarterly AND annual data** - Both 10-Q and 10-K equivalents
✅ **Normalized format** - Consistent JSON structure across all companies
✅ **Fast access** - API responses in milliseconds vs downloading large PDFs
✅ **Rich metadata** - Company info, officers, ratios, technical data
✅ **Multiple exchanges** - US, UK, EU, India, Asia exchanges supported

## Getting Your API Key

### Option 1: Free Demo Key (for testing)

Use the demo key `demo` for testing with these tickers only:
- AAPL.US (Apple)
- TSLA.US (Tesla)
- VTI.US (Vanguard Total Stock Market ETF)
- AMZN.US (Amazon)
- BTC-USD.CC (Bitcoin)
- EURUSD.FOREX (EUR/USD)

### Option 2: Free Tier (limited)

1. Sign up at https://eodhd.com/register
2. Get 20 API calls per day (free)
3. Access end-of-day data for past year only

### Option 3: Paid Plans (recommended for production)

Starting at $19.99/month:
- Unlimited API calls
- Full historical data
- All fundamental data fields
- Real-time data options

[View Plans](https://eodhd.com/pricing)

## Configuration

Add your EODHD API key to `.env`:

```bash
EODHD_API_KEY=your_api_key_here
```

Or use the demo key for testing:

```bash
EODHD_API_KEY=demo
```

## What Data We Fetch

The integration retrieves:

### General Company Information
- Company name, ticker, CIK
- Sector, industry, description
- Officers and key personnel
- Market cap, shares outstanding

### Financial Statements
- **Income Statement** (quarterly & yearly)
  - Revenue, cost of revenue, gross profit
  - Operating income, net income
  - EBITDA, interest expense
  
- **Balance Sheet** (quarterly & yearly)
  - Assets (current, total)
  - Liabilities (current, long-term debt)
  - Equity, retained earnings
  - Cash, receivables, inventory

- **Cash Flow Statement** (quarterly & yearly)
  - Operating cash flow
  - Capital expenditures
  - Investing and financing activities

### Highlights & Metrics
- PE ratio, PEG ratio
- ROA, ROE, profit margins
- Dividend yield, payout ratio
- Book value, earnings per share

## API Endpoint Examples

### Get Company Fundamentals

```
https://eodhd.com/api/fundamentals/AAPL.US?api_token=YOUR_API_KEY&fmt=json
```

### Get Specific Section Only

Filter to reduce response size:

```
# Get only financial statements
https://eodhd.com/api/fundamentals/AAPL.US?api_token=YOUR_API_KEY&filter=Financials

# Get only balance sheet (quarterly)
https://eodhd.com/api/fundamentals/AAPL.US?api_token=YOUR_API_KEY&filter=Financials::Balance_Sheet::quarterly
```

## How FinanceSum Uses EODHD

1. **Company Search**: When you search for "AAPL", we first try EODHD for instant company info
2. **Fetch Filings**: "Fetch Latest Filings" actually calls EODHD to get all quarterly/annual data
3. **Store as "Filings"**: We create 10-Q and 10-K records with data already parsed
4. **Analysis**: Our ratio calculator reads the structured EODHD data directly
5. **AI Summary**: Gemini receives clean, normalized financial data

## Data Flow

```
User searches "AAPL"
    ↓
EODHD API: Get company info
    ↓
User clicks "Fetch Filings"
    ↓
EODHD API: Get all financial statements
    ↓
Store in Supabase (already parsed!)
    ↓
User clicks "Run Analysis"
    ↓
Normalize EODHD format → Internal format
    ↓
Calculate ratios → Compute health score
    ↓
Generate AI analysis with Gemini
    ↓
Display results
```

## Advantages Over SEC EDGAR Parsing

| Feature | SEC EDGAR PDFs | EODHD API |
|---------|---------------|-----------|
| **Data Format** | Unstructured PDF/HTML | Structured JSON |
| **Parsing Needed** | Yes (complex) | No |
| **Speed** | Slow (download + parse) | Fast (API call) |
| **Reliability** | Varies by filing | Consistent |
| **Historical Data** | Must download each filing | All periods in one call |
| **Table Extraction** | Error-prone | Pre-extracted |
| **Normalization** | Manual mapping needed | Already normalized |
| **Cost** | Free but slow | Paid but efficient |

## Rate Limits

- **Free tier**: 20 calls/day
- **Paid plans**: No rate limits
- **Demo key**: No limits for demo tickers

Each company fetch uses **10 API calls** due to data volume.

## Ticker Format

EODHD uses `{SYMBOL}.{EXCHANGE}` format:

- **US stocks**: AAPL.US, TSLA.US, MSFT.US
- **Mexican exchange**: AAPL.MX
- **London**: AAPL.LSE
- **Frankfurt**: AAPL.F

FinanceSum defaults to `.US` for American stocks.

## Error Handling

The implementation includes:

1. **Fallback to EDGAR**: If EODHD fails, we fall back to SEC EDGAR
2. **Graceful degradation**: Missing data points don't break analysis
3. **Retry logic**: Automatic retries on transient failures
4. **Validation**: Checks for data completeness

## Code Reference

Key files:
- `backend/app/services/eodhd_client.py` - EODHD API client
- `backend/app/tasks/fetch.py` - Data fetching with EODHD
- `backend/app/tasks/analyze.py` - EODHD data normalization

## Example: Full EODHD Response

See the structure at: https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo&fmt=json

## Support

- **EODHD Documentation**: https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds/
- **API Status**: https://eodhd.com/status
- **Support**: support@eodhd.com

## Testing

Use the demo key to test:

```bash
# Test in your browser or terminal
curl "https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo&fmt=json"
```

You should see Apple's complete fundamental data returned as JSON.










