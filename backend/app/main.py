"""Main FastAPI application."""
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.api import companies, filings, analysis

settings = get_settings()

app = FastAPI(
    title="FinanceSum API",
    description="Financial analysis platform API",
    version="1.0.0",
    debug=settings.debug
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "FinanceSum API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/favicon.ico")
async def favicon():
    """Favicon endpoint to prevent 404 errors."""
    return Response(status_code=204)


# Include routers
app.include_router(
    companies.router,
    prefix=f"/api/{settings.api_version}/companies",
    tags=["companies"]
)

app.include_router(
    filings.router,
    prefix=f"/api/{settings.api_version}/filings",
    tags=["filings"]
)

app.include_router(
    analysis.router,
    prefix=f"/api/{settings.api_version}/analysis",
    tags=["analysis"]
)



