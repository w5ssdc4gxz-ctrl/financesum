---
name: Restore SEC View & Improve Analysis
overview: ""
todos: []
---

# Restore SEC View & Improve Analysis

1. revert-viewer — Simplify `GET /api/v1/filings/{id}/document` to return the downloaded SEC file immediately (or redirect to the SEC URL) without the new HTML wrapper.
2. enrich-fallback-data — When EODHD returns 403, fall back to internal sample statements (e.g. map GOOG → GOOGL) so filings still carry financial data for analysis.
3. gemini-analysis — Update fallback analysis logic to call Gemini 2.5 Flash Lite (using existing client) and always generate detailed summaries plus all 10 investor persona viewpoints.
4. verify-run — Exercise the fallback flow (fetch filings, run analysis) to confirm the 404 error is resolved and the frontend receives rich results.