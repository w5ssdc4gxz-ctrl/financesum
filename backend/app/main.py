"""Main FastAPI application."""
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, companies, filings, dashboard
from app.config import DEFAULT_CORS_ORIGINS, get_settings

settings = get_settings()

app = FastAPI(
    title="FinanceSum API",
    description="Financial analysis platform API",
    version="1.0.0",
    debug=settings.debug,
)

allowed_origins = settings.cors_origins or DEFAULT_CORS_ORIGINS.copy()
cors_allow_all = settings.cors_allow_all or "*" in allowed_origins

if cors_allow_all:
    cors_kwargs = {
        "allow_origins": ["*"],
        "allow_origin_regex": None,
        "allow_credentials": False,
    }
else:
    cors_kwargs = {
        "allow_origins": allowed_origins,
        "allow_origin_regex": settings.cors_origin_regex,
        "allow_credentials": True,
    }

app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **cors_kwargs,
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

app.include_router(
    dashboard.router,
    prefix=f"/api/{settings.api_version}/dashboard",
    tags=["dashboard"]
)

